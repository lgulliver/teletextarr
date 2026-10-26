"""One encoder per page, shared by everyone watching it.

Plex and Jellyfin both open a channel twice when you tune in (a short probe, then the real stream), and
several people can watch the same page. Each page therefore gets a single ffmpeg process whose output is
fanned out to every connected viewer. It starts on the first viewer and stops `linger` seconds after
the last one leaves, so the probe-then-play pair reuses one encoder instead of starting two.
"""
import collections
import queue
import subprocess
import threading
import time

TS_PACKET = 188
CHUNK = TS_PACKET * 64  # reads stay aligned to whole MPEG-TS packets
BACKLOG_CHUNKS = 16  # recent output replayed to new viewers so they reach a keyframe quickly
VIEWER_QUEUE = 256  # a viewer this far behind is dropped rather than slowing everyone else


def close_viewer(q):
    """Tell a viewer's handler to stop, even if its queue is full."""
    with q.mutex:
        q.queue.clear()
    q.put_nowait(None)


class Broadcast:
    def __init__(self, command, frames, on_idle, linger):
        """command: ffmpeg argv. frames: callable returning the next frame's raw bytes, called once a second."""
        self.command, self.frames, self.on_idle, self.linger = command, frames, on_idle, linger
        self.viewers = set()
        self.backlog = collections.deque(maxlen=BACKLOG_CHUNKS)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.idle_since = None
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        threading.Thread(target=self._feed, daemon=True, name="feed").start()
        threading.Thread(target=self._fan_out, daemon=True, name="fan-out").start()

    def subscribe(self):
        q = queue.Queue(VIEWER_QUEUE)
        with self.lock:
            for chunk in self.backlog:
                q.put_nowait(chunk)
            self.viewers.add(q)
            self.idle_since = None
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.viewers.discard(q)
            if not self.viewers:
                self.idle_since = time.monotonic()

    def viewer_count(self):
        with self.lock:
            return len(self.viewers)

    def _feed(self):
        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                self.proc.stdin.write(self.frames())
                self.proc.stdin.flush()
                with self.lock:
                    idle = self.idle_since is not None and time.monotonic() - self.idle_since >= self.linger
                if idle:
                    break
                self.stopped.wait(max(0.0, 1 - (time.monotonic() - started)))
        except (BrokenPipeError, ValueError, OSError):
            pass
        self.stop()

    def _fan_out(self):
        while chunk := self.proc.stdout.read(CHUNK):
            with self.lock:
                self.backlog.append(chunk)
                for q in list(self.viewers):
                    try:
                        q.put_nowait(chunk)
                    except queue.Full:
                        self.viewers.discard(q)
                        close_viewer(q)
        self.stop()

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.proc.kill()
        self.proc.wait()
        with self.lock:
            for q in self.viewers:
                close_viewer(q)
            self.viewers.clear()
        self.on_idle(self)


class Broadcaster:
    """Hands out the running Broadcast for a page, starting one if needed."""

    def __init__(self, command, frames_for, linger=10.0):
        self.command, self.frames_for, self.linger = command, frames_for, linger
        self.running = {}
        self.lock = threading.Lock()

    def join(self, number):
        """Subscribe to a page's stream. Returns (broadcast, queue); call broadcast.unsubscribe(queue) when done."""
        with self.lock:
            b = self.running.get(number)
            if b is None or b.stopped.is_set():
                b = Broadcast(self.command, self.frames_for(number), lambda done: self._finished(number, done), self.linger)
                self.running[number] = b
            return b, b.subscribe()

    def _finished(self, number, broadcast):
        with self.lock:
            if self.running.get(number) is broadcast:
                del self.running[number]

    def active(self):
        with self.lock:
            return {n: b.viewer_count() for n, b in self.running.items() if not b.stopped.is_set()}
