#!/usr/bin/env python3
"""
Launch Data Reports Generator
Simple script to generate data reports on demand
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def main():
    """Generate a data report"""
    try:
        from data_report_generator import generate_data_report
        
        print("🚀 Launching Data Report Generator...")
        print("=" * 50)
        
        report_path = generate_data_report()
        
        if report_path:
            print("=" * 50)
            print(f"✅ Data report generated successfully!")
            print(f"📁 Report location: {report_path}")
            print(f"🌐 Open in browser: http://localhost:8080/logs/{Path(report_path).name}")
            print("=" * 50)
        else:
            print("❌ Failed to generate data report")
            return 1
            
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure all dependencies are installed")
        return 1
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
