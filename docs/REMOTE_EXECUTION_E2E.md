# E2E Test: Discord Remote Execution

## Prerequisites
- Cuttle running via Electron (`npm start` from electron/)
- Discord bot online (DISCORD_TOKEN in src/.env)
- Default pipeline auto-started (OOBE_Welcome with tool-remote-agent)
- Owner ID configured (`OWNER_ID` in `src/.env`)

## Manual E2E Steps

### 1. Discord Bot Online
- [ ] Start Cuttle (Electron)
- [ ] Check console for "Discord bot (JamBit OS) started"
- [ ] In Discord, verify bot shows as online
- [ ] DM the bot: "hey" → should get LLM chat response

### 2. Remote Code (Owner)
- [ ] As the owner, DM: "create a cron job system and then create a job for 8:30am every morning to send me personalized news over discord"
- [ ] Bot should route to Claude Code, execute on Cuttle project
- [ ] Response should indicate files created / changes made

### 3. Explicit /claude Command
- [ ] DM: "/claude create a hello.txt file with Hello World"
- [ ] Should execute via Claude Code
- [ ] Verify file created in Cuttle project

### 4. Filesystem via Claude
- [ ] DM: "list the files in the src directory"
- [ ] Claude Code runs in project context → can read directory
- [ ] Response should show file listing

### 5. Non-Owner Chat
- [ ] As non-owner user, DM: "create a file" 
- [ ] Should fall back to LLM (no execution)
- [ ] LLM explains remote execution requires owner

## Automated E2E (Future)
- Use Discord bot test account + mocked API for CI
- Or Playwright + local Flask for pipeline trigger tests
