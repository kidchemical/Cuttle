# Skills Registry

Cuttle supports a **skills registry** so you can install pipeline templates (and optionally MCP configs) from a curated list.

## Concepts

- **Skill** – A pipeline template or MCP server config. Has `id`, `name`, `description`, `type` (`pipeline_template` or `mcp_config`).
- **Registry** – Stored in `src/data/skills_registry.json`. Default list includes at least one pipeline template (e.g. OOBE Welcome).
- **Install** – For `pipeline_template`, saves the pipeline JSON to `src/pipelines/<name>.json`. You can then load and run it in the Node Editor.

## API

- **GET /api/skills/list** – List skills (id, name, description, type).
- **GET /api/skills/<skill_id>** – Get full skill (for preview).
- **POST /api/skills/install** – Install a skill. Body: `{ "skill_id": "oobe_welcome", "pipeline_name": "MyPipeline" }`. `pipeline_name` is optional; default is the skill’s pipeline name.

## Skills page

Open **/skills_page.html** to see the registry and click **Install** for a skill. After install you are redirected to the Node Editor with the pipeline name in the query string (load it from the pipeline list).

## Adding skills

Edit `src/data/skills_registry.json` or use the `add_skill()` function in `api/skills_registry.py` to add entries. Each skill must have:

- **id** – Unique string.
- **name** – Display name.
- **description** – Short description.
- **type** – `pipeline_template` or `mcp_config`.
- **pipeline** (for pipeline_template) – Full pipeline JSON (name, nodes, connections, triggers).

This is pipeline-centric: skills are pipeline templates you can install and then edit in the Node Editor.
