# encoding: utf-8

import random
import zmq
import time

context = zmq.Context()
worker = context.socket(zmq.DEALER)
worker.setsockopt(zmq.IDENTITY, str(random.randint(0, 8000)).encode("utf-8"))
worker.connect("tcp://localhost:5556")
start = False
worker.send(b"Hello")
while True:
    if start:
        worker.send("recording data: %s" % random.randint(0, 100))
        time.sleep(0.5)
    request = worker.recv()
    if request == "START":
        start = True
    if request == "STOP":
        start = False
    if request == "END":
        print("A is finishing")
        break