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
    PerSocketData._test_reset()
    MainContext._test_reset()
    self._sock1, self._sock2 = two_new_sockets()

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
    PerSocketData._test_reset()
    MainContext._test_reset()
    self._sock1, self._sock2 = two_new_sockets()
    self._ctx = MainContext.default()

    self._ml = MainLoop()

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

  def testRemoveInDispatch(self):
    self._sock1.bind(("", 0))
    self._sock1.listen(1)
    _, port = self._sock1.getsockname()
    
    called = []
    def callback(fd, condition):
      called.append(True)
      source_remove(sid)
    sid = io_add_watch(self._sock1, IO_IN, callback)
    
    self._sock2.connect(("localhost", port))

    timeout_add(500, self._ml.quit)
    self._ml.run()
    
    self.assertRaises(KeyError, source_remove, sid)
    

class TestGobject(unittest.TestCase):
  def setUp(self):
    PerSocketData._test_reset()
    MainContext._test_reset()
    self._sock1, self._sock2 = two_new_sockets()
    self._ctx = MainContext.default()
    
    self._ml = MainLoop()

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

  def testSocketAcceptIoIn(self):
    self._sock1.bind(("", 0))
    self._sock1.listen(1)
    _, port = self._sock1.getsockname()
    
    called = []
    def callback(fd, condition):
      called.append(True)
    sid = io_add_watch(self._sock1, IO_IN, callback)
    
    self._sock2.connect(("localhost", port))

    timeout_add(500, self._ml.quit)
    self._ml.run()
    
    self.assertEqual(1, len(called))

  def testSocketAcceptIoOut(self):
    self._sock1.bind(("", 0))
    self._sock1.listen(1)
    _, port = self._sock1.getsockname()
    
    called = []
    def callback(fd, condition):
      called.append(True)
    sid = io_add_watch(self._sock1, IO_OUT, callback)
    
    self._sock2.connect(("localhost", port))

    timeout_add(500, self._ml.quit)
    self._ml.run()
    
    self.assertEqual(0, len(called))

  def testTwoWatchesOneFd(self):
    """Multiple watches on the same socket should coexist."""
    self._sock1.bind(("", 0))
    self._sock1.listen(1)
    _, port = self._sock1.getsockname()
    
    self._sock2.connect(("localhost", port))
    
    sock1c, _ = self._sock1.accept()
    
    called_read = []
    def handle_read(fd, condition):
      called_read.append(True)
      sock1c.recv(4096)
      return True
    sid1 = io_add_watch(sock1c, IO_IN, handle_read)
    
    called_hup = []
    sid2 = 0
    def handle_hup(fd, condition):
      called_hup.append(True)
      return True
    sid2 = io_add_watch(sock1c, IO_HUP, handle_hup)
    
    self._sock2.send("a" * 1000)
    self._sock2.close()
    
    timeout_add(500, self._ml.quit)
    self._ml.run()
    
    self.assertEqual(1, len(called_read))
    self.assertEqual(1, len(called_hup))

  def testLazyRecv(self):
    """IO_IN handler should be called repeatedly for incomplete recv()'s."""
    self._sock1.bind(("", 0))
    self._sock1.listen(1)
    _, port = self._sock1.getsockname()
    
    self._sock2.connect(("localhost", port))
    
    sock1c, _ = self._sock1.accept()
    
    called_read = []
    def handle_read(fd, condition):
      called_read.append(True)
      sock1c.recv(10)
      return True
    sid = io_add_watch(sock1c, IO_IN, handle_read)
    
    self._sock2.send("a" * 36)
    
    timeout_add(500, self._ml.quit)
    self._ml.run()
    
    self.assertEqual(4, len(called_read))

if __name__ == "__main__":
  unittest.main()
