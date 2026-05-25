import sys
import os

path = '/home/ktlee/lotto'
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application
