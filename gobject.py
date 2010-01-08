#!/usr/bin/python
"""
Pure Python implementation of (very small) subset of glib/gobject.
Copyright (C) 2010 Catalin Patulea <cat@vv.carleton.ca>

License: GPL-2

Heavily inspired from glib (glib/gmain.c, glib/gwin32.c).

TODO:
 - report errors using GError
 - source priorities
 - IO_IN should always be delivered before (or with) IO_HUP (depends on source
   priorities)
"""
from ctypes import *
from win32file import WSAEventSelect, FD_READ, FD_WRITE, FD_CLOSE, FD_ACCEPT, FD_CONNECT
from win32event import CreateEvent, SetEvent, WaitForMultipleObjects, QS_ALLINPUT, WAIT_OBJECT_0, WAIT_TIMEOUT, WAIT_FAILED, INFINITE
from win32gui import PeekMessage, TranslateMessage, DispatchMessage
from win32con import MWMO_ALERTABLE
from win32api import SetConsoleCtrlHandler, Sleep
import pywintypes
import logging
import sys
import socket
import time

def _net_events_str(net_events):
  flags = ["FD_READ", "FD_WRITE", "FD_CLOSE", "FD_ACCEPT", "FD_CONNECT"]
  return "|".join(f for f in flags if net_events & globals()[f])

class WSANETWORKEVENTS(Structure): 
  _fields_ = [('lNetworkEvents', c_long), 
              ('iErrorCode', c_int * 10) # 10 = FD_MAX_EVENTS 
             ]

SOCKET_ERROR = -1

WSAGetLastError = windll.ws2_32.WSAGetLastError
def WSAEnumNetworkEvents(fd, event):
  net_events = WSANETWORKEVENTS()
  rc = windll.ws2_32.WSAEnumNetworkEvents(fd, event.handle, byref(net_events))
  if rc == SOCKET_ERROR:
    raise pywintypes.error(WSAGetLastError(), "WSAEnumNetworkEvents")
  return net_events

IO_IN = 1
IO_OUT = 4
IO_ERR = 8
IO_HUP = 16

def _io_condition_str(condition):
  flags = ["IO_IN", "IO_OUT", "IO_ERR", "IO_HUP"]
  return "|".join(f for f in flags if condition & globals()[f])

class PerSocketData(object):
  _for_fd = {}

  @staticmethod
  def for_socket(sock):
    fd = sock.fileno()
    if fd not in PerSocketData._for_fd:
      PerSocketData._for_fd[fd] = PerSocketData(fd)

    return PerSocketData._for_fd[fd]

  @staticmethod
  def _test_reset():
    PerSocketData._for_fd = {}

  def __init__(self, fd):
    self._fd = fd
    self._event = CreateEvent(None, False, False, None)
    self._watches = {}

  def add_watch(self, source, condition):
    self._watches[source] = condition
    self._select_net_events()

  def remove_watch(self, source):
    del self._watches[source]
    if not self._watches:
      del PerSocketData._for_fd[self._fd]
    self._select_net_events()

  def _select_net_events(self):
    events = 0
    for condition in self._watches.itervalues():
      events |= condition

    net_events = 0
    if events & IO_IN:
      net_events |= FD_READ | FD_ACCEPT
    if events & IO_OUT:
      net_events |= FD_WRITE | FD_CONNECT
    if events & IO_HUP:
      net_events |= FD_CLOSE

    WSAEventSelect(self._fd, self._event, net_events)
    
    print "event select 0x%04x" % net_events, _net_events_str(net_events)

  def prepare(self):
    self._enumed = False
    return self._event, sys.maxint # timeout

  def check(self):
    if not self._enumed: # enumerate only once per socket
      net_events = WSAEnumNetworkEvents(self._fd, self._event)
      
      print "enum events 0x%04x" % net_events.lNetworkEvents, \
            _net_events_str(net_events.lNetworkEvents)

      self._revents = 0
      if net_events.lNetworkEvents & (FD_READ | FD_ACCEPT):
        self._revents |= IO_IN
      if net_events.lNetworkEvents & (FD_WRITE | FD_CONNECT):
        self._revents |= IO_OUT
      if net_events.lNetworkEvents & FD_CLOSE:
        self._revents |= IO_HUP

      for error in net_events.iErrorCode:
        if error:
          self._revents |= IO_ERR

      self._enumed = True

    return bool(self._revents)

  def needs_dispatch(self, source):
    if not self._enumed:
      raise ValueError("Must call check() first")

    return self._revents & self._watches[source]

  def __str__(self):
    s = ["<PerSocketData fd=%d watches={" % self._fd]
    s.append(", ".join("source %s: %s" % (source, _io_condition_str(condition))
                       for source, condition in self._watches.iteritems()))
    s.append("}>")
    return "".join(s)

  __repr__ = __str__

class Source(object):
  def __init__(self, callback, args):
    self._callback = callback
    self._args = args

  def prepare(self):
    raise NotImplemented
  def check(self):
    raise NotImplemented
  def dispatch(self):
    cb = self._callback
    return cb(*self._args)

  def preremove(self): # used only by SocketSource
    pass

class SocketSource(Source):
  def __init__(self, sock, condition, callback, args):
    super(SocketSource, self).__init__(callback, args)
    self._socket_data = PerSocketData.for_socket(sock)
    self._condition = condition
    self._socket_data.add_watch(self, condition)

  def prepare(self):
    return self._socket_data.prepare()

  def check(self):
    return self._socket_data.check()

  def dispatch(self):
    if self._socket_data.needs_dispatch(self):
      return super(SocketSource, self).dispatch()
    else:
      return True

  def preremove(self):
    self._socket_data.remove_watch(self)

class TimeoutSource(Source):
  def __init__(self, ms, callback, args):
    super(TimeoutSource, self).__init__(callback, args)
    self._interval = ms * 0.001
    self._expiration = time.time() + self._interval

  def prepare(self):
    now = time.time()
    timeout = self._expiration - now

    if timeout < 0:
      timeout = 0
    elif timeout > self._interval: # system time was set backwards
      self._expiration = now + self._interval
      timeout = self._interval

    return None, int(1000.0 * timeout)

  def check(self):
    return time.time() >= self._expiration

  def dispatch(self):
    cb_result = super(TimeoutSource, self).dispatch()
    self._expiration = time.time() + self._interval
    return cb_result

class IdleSource(Source):
  def prepare(self):
    return None, 0

  def check(self):
    return True

class CtrlCSource(Source):
  def __init__(self):
    # callback never used because check() always returns False
    super(CtrlCSource, self).__init__(None, None)
    self._event = CreateEvent(None, False, False, None)
    SetConsoleCtrlHandler(self._ctrlc_handler, True)

  def _ctrlc_handler(self, code):
    SetEvent(self._event)
    return False # process Python Ctrl-C handler

  def prepare(self):
    return self._event, sys.maxint

  def check(self):
    # just need to return from WaitForMultipleObjects, Python will raise
    # KeyboardInterrupt
    return False

class MainContext(object):
  _default = None

  @staticmethod
  def default():
    if MainContext._default is None:
      MainContext._default = MainContext()
    return MainContext._default

  @staticmethod
  def _test_reset():
    MainContext._default = None

  def __init__(self):
    self._next_id = 0
    self._sources = {}

  def attach(self, source):
    id = self._next_id
    self._next_id += 1

    self._sources[id] = source
    return id

  def detach(self, source_id):
    self._sources[source_id].preremove()
    del self._sources[source_id]

  def query(self):
    events = set()
    timeout = sys.maxint

    for source in self._sources.itervalues():
      event, source_timeout = source.prepare()

      if event:
        events.add(event)
      timeout = min(timeout, source_timeout)

    return events, timeout

  def check_and_dispatch(self):
    for sid, source in self._sources.items():
      if source.check():
        need_destroy = not source.dispatch()
        if need_destroy:
          self.detach(sid)

class MainLoop(object):
  def __init__(self):
    self._context = MainContext.default()
    self._is_running = False

  def quit(self):
    self._is_running = False

  def run(self):
    self._is_running = True

    while self._is_running:
      self._iterate()

  def _iterate(self):
    events, timeout = self._context.query()

    events = list(events)
    if timeout == sys.maxint:
      timeout = INFINITE

    print "waiting on %d events, timeout %d" % (len(events), timeout)

    if events:
      rc = WaitForMultipleObjects(events, False, timeout)
      if rc == WAIT_FAILED:
        raise pywintypes.error(GetLastError(), "WaitForMultipleObjects")
      # else: # WAIT_TIMEOUT or WAIT_OBJECT_0+i
    else:
      Sleep(timeout)

    self._context.check_and_dispatch()

def io_add_watch(sock, condition, callback, *args):
  source = SocketSource(sock, condition, callback, args)
  sid = MainContext.default().attach(source)
  return sid

def idle_add(callback, *args):
  source = IdleSource(callback, args)
  sid = MainContext.default().attach(source)
  return sid

def ctrlc_add():
  source = CtrlCSource()
  sid = MainContext.default().attach(source)
  return sid

def timeout_add(ms, callback, *args):
  source = TimeoutSource(ms, callback, args)
  sid = MainContext.default().attach(source)
  return sid

# TODO: child_watch_add, debatable since it's only used by Linux-only player
# adapters.

def source_remove(sid):
  ctx = MainContext.default().detach(sid)

if __name__ == "__main__":
  ml = MainLoop()

  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.setblocking(False)
  s.bind(("", 1234))
  s.listen(5)

  def handle_io():
    print "io"
    return False
  sid1 = io_add_watch(s, IO_IN|IO_OUT, handle_io)
  sid2 = io_add_watch(s, IO_HUP, handle_io)

  ctrlc_add()

  def handle_idle():
    print "idle"
    import time
    time.sleep(1)
    return True
  sid3 = idle_add(handle_idle)
  source_remove(sid3)

  def handle_timer():
    print "tick"
    return True
  timeout_add(10000, handle_timer)

  #source_remove(sid1)
  #source_remove(sid2)

  #print MainContext().default()._sources

  print MainContext().default()._sources
  print PerSocketData._for_fd

  ml.run()
