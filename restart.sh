#!/bin/sh

killall node
killall python
nohup node app.js $1 > node.out 2>&1 &
nohup python runner.py $1 > python.out 2>&1 &
