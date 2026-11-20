# Lets the tests import the project's modules (config, scoring, ...).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
