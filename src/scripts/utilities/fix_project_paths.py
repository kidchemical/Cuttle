#!/usr/bin/env python3
"""
Fix Project Paths Utility
Helps clean up and fix invalid project paths in the database.
Useful when moving the project to a different machine or network location.
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from managers.project_manager import project_manager

def main():
    print("=" * 60)
    print("Fix Project Paths Utility")
    print("=" * 60)
    print()
    
    # Get all projects
    projects = project_manager.get_projects()
    
    if not projects:
        print("No projects found in the database.")
        return
    
    print(f"Found {len(projects)} project(s) in the database:")
    print()
    
    invalid_projects = []
    
    for project in projects:
        project_path = Path(project['path'])
        exists = project_path.exists()
        status = "✅ EXISTS" if exists else "❌ INVALID"
        active = "🟢 ACTIVE" if project['is_active'] else ""
        
        print(f"{status} {active}")
        print(f"  Name: {project['name']}")
        print(f"  Path: {project['path']}")
        print(f"  Type: {project['type']}")
        print()
        
        if not exists:
            invalid_projects.append(project)
    
    if not invalid_projects:
        print("All project paths are valid! ✅")
        return
    
    print(f"\nFound {len(invalid_projects)} project(s) with invalid paths.")
    print()
    print("Options:")
    print("1. Delete invalid projects")
    print("2. Show project manager commands")
    print("3. Remap paths (prefix / explicit map) — see migrate_project_paths.py")
    print("4. Exit without changes")
    print()
    
    choice = input("Enter your choice (1-4): ").strip()
    
    if choice == "1":
        confirm = input(f"\nAre you sure you want to delete {len(invalid_projects)} project(s)? (yes/no): ").strip().lower()
        if confirm == "yes":
            for project in invalid_projects:
                success = project_manager.delete_project(project['id'])
                if success:
                    print(f"✅ Deleted: {project['name']}")
                else:
                    print(f"❌ Failed to delete: {project['name']}")
            print("\nCleanup complete!")
            
            # If we deleted the active project, set the first valid project as active
            remaining_projects = project_manager.get_projects()
            if remaining_projects:
                current = project_manager.get_current_project()
                if not current:
                    project_manager.switch_to_project(remaining_projects[0]['id'])
                    print(f"\nSwitched to: {remaining_projects[0]['name']}")
        else:
            print("Cancelled.")
    
    elif choice == "2":
        print("\nProject Manager Commands:")
        print("-" * 60)
        print("To add a new project:")
        print("  from managers.project_manager import project_manager")
        print("  project_manager.add_local_project('My Project', '/path/to/project')")
        print()
        print("To switch to a project:")
        print("  project_manager.switch_to_project(project_id)")
        print()
        print("To update a project path:")
        print("  # First delete the old project, then add it again with new path")
        print("  project_manager.delete_project(project_id)")
        print("  project_manager.add_local_project('Project Name', '/new/path')")
        print("-" * 60)
    
    elif choice == "3":
        print("\nPath migration (non-interactive):")
        print("-" * 60)
        print("  python src/scripts/utilities/migrate_project_paths.py --from OLD --to NEW")
        print("  python src/scripts/utilities/migrate_project_paths.py --map OLD=NEW [--apply]")
        print("  Add --apply to write; omit for dry-run preview.")
        print("-" * 60)

    elif choice == "4":
        print("Exiting without changes.")
    
    else:
        print("Exiting without changes.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

