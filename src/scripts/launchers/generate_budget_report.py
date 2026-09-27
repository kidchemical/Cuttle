#!/usr/bin/env python3
"""
Simple script to generate a Cuttle Budget Report
"""

import sys
import os
from pathlib import Path

# Add the current directory to the path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from budget_report_generator import generate_budget_report

def main():
    """Generate a budget report"""
    print("Generating Cuttle Budget Report...")
    
    try:
        report_path = generate_budget_report()
        if report_path:
            print(f"Budget report generated successfully!")
            print(f"Report location: {report_path}")
            print(f"Open in browser: file:///{os.path.abspath(report_path)}")
        else:
            print("Failed to generate budget report")
            return 1
    except Exception as e:
        print(f"Error generating budget report: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
