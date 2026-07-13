#!/bin/sh
pip install pyinstaller
pyinstaller --onefile gron.py
cp dist/gron .