#!/usr/bin/env python3
"""
Update Project Paths Utility
Fixes project paths that have moved to a different drive
"""

import sqlite3
import sys
from pathlib import Path

def update_project_paths(db_path: str, old_drive: str, new_drive: str):
    """Update project paths from one drive to another"""
    
    old_drive = old_drive.upper().rstrip(":\\") + ":"
    new_drive = new_drive.upper().rstrip(":\\") + ":"
    
    print(f"Updating project paths from {old_drive} to {new_drive}...")
    print("=" * 60)
    
    try:
        # Connect to database
        conn = sqlite3.connect(db_path, timeout=30.0)
        cursor = conn.cursor()
        
        # Find projects with old drive
        cursor.execute('''
            SELECT id, name, path 
            FROM projects 
            WHERE path LIKE ?
        ''', (f"{old_drive}%",))
        
        projects_to_update = cursor.fetchall()
        
        if not projects_to_update:
            print(f"No projects found with paths starting with {old_drive}")
            conn.close()
            return
        
        print(f"Found {len(projects_to_update)} project(s) to update:\n")
        
        # Update each project
        updated_count = 0
        for project_id, name, old_path in projects_to_update:
            # Replace the drive letter
            new_path = old_path.replace(old_drive, new_drive, 1)
            
            print(f"Project: {name}")
            print(f"  OLD: {old_path}")
            print(f"  NEW: {new_path}")
            
            # Check if new path exists
            if Path(new_path).exists():
                print(f"  ✅ Path exists")
            else:
                print(f"  ⚠️  Warning: Path does not exist")
            
            # Update the database
            cursor.execute('''
                UPDATE projects 
                SET path = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (new_path, project_id))
            
            updated_count += 1
            print()
        
        # Commit changes
        conn.commit()
        conn.close()
        
        print("=" * 60)
        print(f"✅ Successfully updated {updated_count} project path(s)")
        print(f"\nYou can now restart the launcher without warnings.")
        
    except Exception as e:
        print(f"❌ Error updating project paths: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def main():
    # Default to updating F: to E:
    if len(sys.argv) >= 3:
        old_drive = sys.argv[1]
        new_drive = sys.argv[2]
    else:
        old_drive = "F:"
        new_drive = "E:"
    
    # Find the projects.db in the project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent
    db_path = project_root / "projects.db"
    
    if not db_path.exists():
        print(f"❌ Error: Database not found at {db_path}")
        sys.exit(1)
    
    print(f"Database: {db_path}")
    update_project_paths(str(db_path), old_drive, new_drive)

if __name__ == "__main__":
    main()

