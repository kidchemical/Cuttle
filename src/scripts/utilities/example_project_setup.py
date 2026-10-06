#!/usr/bin/env python3
"""
Example script demonstrating the Project Management Suite
This shows how to add local and remote projects to Cuttle
"""

from project_manager import project_manager
import os

def main():
    print("🚀 Cuttle Project Management Suite - Example Setup")
    print("=" * 60)
    
    # Example 1: Add the current Cuttle project as a local project
    print("\n📁 Adding current Cuttle project as local project...")
    current_dir = os.getcwd()
    success = project_manager.add_local_project(
        name="Cuttle Main",
        path=current_dir,
        description="Main Cuttle development project",
        tags=["python", "discord", "ai", "automation"]
    )
    
    if success:
        print("✅ Successfully added Cuttle Main project")
    else:
        print("❌ Failed to add Cuttle Main project")
    
    # Example 2: Add a GitHub project (example)
    print("\n🐙 Adding example GitHub project...")
    github_success = project_manager.add_github_project(
        name="Example GitHub Repo",
        repo_url="https://github.com/microsoft/vscode.git",
        description="Example GitHub repository for testing",
        tags=["typescript", "editor", "vscode"]
    )
    
    if github_success:
        print("✅ Successfully added GitHub project")
    else:
        print("❌ Failed to add GitHub project (this is expected if you don't have git configured)")
    
    # Example 3: Add a GitLab project (example)
    print("\n🦊 Adding example GitLab project...")
    gitlab_success = project_manager.add_gitlab_project(
        name="Example GitLab Repo",
        repo_url="https://gitlab.com/gitlab-org/gitlab.git",
        description="Example GitLab repository for testing",
        tags=["ruby", "rails", "gitlab"]
    )
    
    if gitlab_success:
        print("✅ Successfully added GitLab project")
    else:
        print("❌ Failed to add GitLab project (this is expected if you don't have git configured)")
    
    # Show all projects
    print("\n📋 Current Projects:")
    projects = project_manager.get_projects()
    
    if projects:
        for project in projects:
            print(f"  • {project['name']} ({project['type']})")
            print(f"    Path: {project['path']}")
            print(f"    Description: {project['description']}")
            print(f"    Tags: {', '.join(project['tags'])}")
            print()
    else:
        print("  No projects found")
    
    # Show project statistics
    print("📊 Project Statistics:")
    stats = project_manager.get_project_stats()
    print(f"  Total Projects: {stats['total_projects']}")
    print(f"  By Type: {stats['by_type']}")
    print(f"  Recent Activity: {stats['recent_activity']} actions in last 7 days")
    print(f"  Current Project: {stats['current_project'] or 'None'}")
    
    print("\n🎉 Project setup complete!")
    print("\nNext steps:")
    print("1. Start the web server: python web_chat_api.py")
    print("2. Open http://localhost:8080/git_graph_page.html")
    print("3. Use the project selector to switch between projects")
    print("4. Add your own projects using the 'Add Project' button")

if __name__ == "__main__":
    main()
