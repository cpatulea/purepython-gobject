#!/usr/bin/python
import unittest
import socket

from gobject import *

# PerSocketData sucks because its state persists between tests, so each test
# must reset it with .test_reset().

def two_new_sockets():
  return [socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0) for _ in range(2)]

def nop(*args, **kwargs):
  pass

class TestSocketSource(unittest.TestCase):
  def setUp(self):
    self._sock1, self._sock2 = two_new_sockets()
    PerSocketData._test_reset()

  def testOneWatchOneFd(self):
    sid = 1234
    s = SocketSource(self._sock1, IO_IN, nop, [])
    self.assertEqual(1, len(PerSocketData.for_socket(self._sock1)._watches))

    s.preremove()
    self.assertEqual(0, len(PerSocketData._for_fd))

  def testTwoWatchesOneFd(self):
    sid1 = 1234
    s1 = SocketSource(self._sock1, IO_OUT, nop, [])
    self.assertEqual(1, len(PerSocketData.for_socket(self._sock1)._watches))
    self.assertEqual(1, len(PerSocketData._for_fd))

    sid2 = 4567
    s2 = SocketSource(self._sock1, IO_OUT, nop, [])
    self.assertEqual(2, len(PerSocketData.for_socket(self._sock1)._watches))
    self.assertEqual(1, len(PerSocketData._for_fd))

    event1, _ = s1.prepare()
    event2, _ = s2.prepare()
    self.assertEqual(event1, event2)

    s1.preremove()
    s2.preremove()

  def testTwoWatchesTwoFds(self):
    sid1 = 1234
    s1 = SocketSource(self._sock1, IO_OUT, nop, [])
    self.assertEqual(1, len(PerSocketData.for_socket(self._sock1)._watches))
    self.assertEqual(1, len(PerSocketData._for_fd))

    sid2 = 4567
    s2 = SocketSource(self._sock2, IO_OUT, nop, [])
    self.assertEqual(1, len(PerSocketData.for_socket(self._sock2)._watches))
    self.assertEqual(2, len(PerSocketData._for_fd))

    event1, _ = s1.prepare()
    event2, _ = s2.prepare()
    self.assertNotEqual(event1, event2)

    s1.preremove()
    s2.preremove()

class TestMainContext(unittest.TestCase):
  def setUp(self):
    self._sock1, self._sock2 = two_new_sockets()
    self._ctx = MainContext.default()
    PerSocketData._test_reset()

  def testOneWatchOneFd(self):
    sid = io_add_watch(self._sock1, IO_IN, nop)
    events, _ = self._ctx.query()
    self.assertEqual(1, len(events))
    source_remove(sid)

  def testTwoWatchesTwoFds(self):
    sid1 = io_add_watch(self._sock1, IO_OUT, nop)
    sid2 = io_add_watch(self._sock2, IO_HUP|IO_ERR, nop)
    events, _ = self._ctx.query()
    self.assertEqual(2, len(events))
    source_remove(sid1)
    source_remove(sid2)

  def testTwoWatchesOneFd(self):
    sid1 = io_add_watch(self._sock1, IO_OUT, nop)
    sid2 = io_add_watch(self._sock1, IO_HUP|IO_ERR, nop)
    events, _ = self._ctx.query()
    self.assertEqual(1, len(events))
    source_remove(sid1)
    source_remove(sid2)

class TestGobject(unittest.TestCase):
  def setUp(self):
    self._sock1, self._sock2 = two_new_sockets()
    self._ctx = MainContext.default()
    PerSocketData._test_reset()

  def testOneIoAddWatch(self):
    sid = io_add_watch(self._sock1, IO_IN, nop)
    self.assert_(self._ctx._sources[sid])
    source_remove(sid)

  def _testTwoIoAddWatchWithFds(self, fd1, fd2):
    sid1 = io_add_watch(fd1, IO_IN|IO_OUT, nop)
    sid2 = io_add_watch(fd2, IO_OUT, nop)
    self.assert_(self._ctx._sources[sid1])
    self.assert_(self._ctx._sources[sid2])

    source_remove(sid1)
    self.assert_(sid1 not in self._ctx._sources)
    self.assert_(self._ctx._sources[sid2])

    source_remove(sid2)
    self.assert_(sid2 not in self._ctx._sources)

  def testTwoIoAddWatchSameFd(self):
    self._testTwoIoAddWatchWithFds(self._sock1, self._sock1)

  def testTwoIoAddWatchDifferentFd(self):
    self._testTwoIoAddWatchWithFds(self._sock1, self._sock2)

  def testIdleAdd(self):
    sid = idle_add(nop)
    self.assertEqual(1, len(self._ctx._sources))
    self.assert_(self._ctx._sources[sid])
    source_remove(sid)


if __name__ == "__main__":
  unittest.main()
