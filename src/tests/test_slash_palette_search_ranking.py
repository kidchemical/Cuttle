"""Exercise name-first search through the actual assembled command palette."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def run_js(body):
    page = (ROOT / "src/web/js/chat_page.js").read_text()
    names = ["filterSlashPaletteItems", "buildCodexEffortPaletteItems",
             "buildCodexModelPaletteItems", "buildOpenCodeModelPaletteItems",
             "buildRestartPaletteItems"]
    blocks = []
    for name in names:
        start = page.index(f"    function {name}(")
        end = page.index("\n    function ", start + 10)
        blocks.append(page[start:end])
    setup = """
const CuttleChatSlash = require('./src/web/js/chat_slash.js');
const S = CuttleChatSlash;
const slashPaletteItemMatches = S.slashPaletteItemMatches;
const slashPaletteTypeBucket = S.slashPaletteTypeBucket;
const slashPaletteTypeFilter = 'all';
const slashPaletteSupplement = {codexModels: [], codexCommonEfforts: ['low','medium','high','xhigh'],
    opencodeModels: []};
const agentSupportsEffort = () => true;
const hasActiveCodexAgentChip = () => true;
const hasActiveOpenCodeAgentChip = () => true;
const loadCodexEffortForPalette = () => {};
const loadCodexModelsForPalette = () => {};
const loadOpenCodeModelsForPalette = () => {};
let commands = S.SLASH_COMMANDS;
let cursorCommands = S.CURSOR_AGENT_SLASH_COMMANDS;
let projectCommands = [];
let stars = [];
const slashCommandsForCurrentMode = () => commands;
const cursorAgentSlashCommandsForPalette = () => cursorCommands;
const harnessUsageSlashCommandsForPalette = () => [];
const buildProjectPaletteItems = () => [];
const buildProjectCommandPaletteItems = () => projectCommands;
const buildCursorModelPaletteItems = () => [];
const buildMuseModelPaletteItems = () => [];
const buildMuseEffortPaletteItems = () => [];
const buildHermesModelPaletteItems = () => [];
const buildHermesEffortPaletteItems = () => [];
const buildOpenCodeEffortPaletteItems = () => [];
const buildClaudeModelPaletteItems = () => [];
const buildClaudeEffortPaletteItems = () => [];
const readStarredSlashPrefixes = () => stars;
const readStarredProject = () => null;
const normalizeStarredProjectPath = () => '';
"""
    result = subprocess.run(["node", "-e", setup + "\n".join(blocks) + body],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("query", ["high", "hi", "effort high", "codex effort high", "  HIGH  "])
def test_effort_name_beats_model_description_and_favorites(query):
    result = run_js("""
slashPaletteSupplement.codexModels = [
  {id:'gpt-example', label:'Example model', description:'High intelligence; high reasoning effort'}
];
commands = [{prefix:'/smart', label:'Smart', hint:'High quality', stickySession:true}];
stars = ['/smart'];
process.stdout.write(JSON.stringify(filterSlashPaletteItems(%s).items));
""" % json.dumps(query))
    assert result[0]["prefix"] == "/codex effort high"


def test_every_registered_command_name_outranks_description_matches():
    result = run_js("""
const failures = [];
for (const item of [...S.SLASH_COMMANDS, ...S.CURSOR_AGENT_SLASH_COMMANDS]) {
    const query = item.prefix.trim().slice(1);
    commands = [item, {prefix:'/unrelated', label:'A unrelated', hint:query, stickySession:true}];
    cursorCommands = [];
    stars = ['/unrelated'];
    if (filterSlashPaletteItems(query).items[0].prefix !== item.prefix) failures.push(query);
}
process.stdout.write(JSON.stringify(failures));
""")
    assert result == []


def test_aliases_reserved_names_and_description_discovery():
    result = run_js("""
projectCommands = S.buildProjectCommandPaletteItems([
  {name:'deploy', title:'Publish release', aliases:['ship'], description:'Upload artifacts'},
  {name:'help', title:'Project guide', reserved_collision:true},
]);
commands = [{prefix:'/upload', label:'A upload', hint:'ship deploy help', stickySession:true}];
cursorCommands = [];
stars = ['/upload'];
const first = q => filterSlashPaletteItems(q).items[0].prefix;
process.stdout.write(JSON.stringify({alias:first('ship'), name:first('deploy'),
    reserved:first('help'), description:first('artifacts'),
    missing:filterSlashPaletteItems('ship nonexistent').items.filter(i => !i.disabled)}));
""")
    assert result == {"alias": "/deploy ", "name": "/deploy ", "reserved": "/cmd help ",
                      "description": "/deploy ", "missing": []}


def test_exact_subcommands_and_compact_model_names():
    result = run_js("""
slashPaletteSupplement.codexModels = [
  {id:'claude-sonnet-4.6', label:'★ Claude Sonnet 4.6 (current)'},
  {id:'other', label:'A other', description:'Supports sonnet4.6 style prompts'},
];
process.stdout.write(JSON.stringify({
  compact:filterSlashPaletteItems('sonnet4.6').items[0].modelId,
  restart:filterSlashPaletteItems('restart graceful').items[0].prefix,
  refreshMiss:buildCodexModelPaletteItems('refresh nonexistent').filter(i => !i.disabled),
}));
""")
    assert result == {"compact": "claude-sonnet-4.6", "restart": "/restart graceful",
                      "refreshMiss": []}


@pytest.mark.parametrize("agent", ["codex", "opencode"])
def test_model_name_hits_survive_catalog_limit(agent):
    result = run_js("""
const agent = %s;
slashPaletteSupplement[agent + 'Models'] = Array.from({length:45}, (_, i) => ({
  id:'other-' + i, label:'Other ' + i, description:'Needle in description'
})).concat({id:'needle', label:'Needle'});
const builder = agent === 'codex' ? buildCodexModelPaletteItems : buildOpenCodeModelPaletteItems;
process.stdout.write(JSON.stringify(builder('needle').map(i => i.modelId)));
""" % json.dumps(agent))
    assert "needle" in result
