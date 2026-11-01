"""Standalone durable LangSmith sender; does not execute SOC or models."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.telemetry.sync import main

if __name__ == '__main__':
    main()
