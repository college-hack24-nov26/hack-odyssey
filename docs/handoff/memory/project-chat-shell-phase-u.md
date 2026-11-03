---
name: project-chat-shell-phase-u
description: /chat is now a Claude-desktop-style shell (sidebar + panes registry in chat-shell.js); where things live and what is still local-only
metadata:
  node_type: memory
  type: project
  originSessionId: c74f6232-3a85-4a40-817f-2c9046b69871
  modified: 2026-11-03T19:20:26.431Z
---

The `/chat` page (simlab/dashboard/static/chat*.js/css) has a left sidebar (projects -> chats, search, new project/chat) and a right dock of tabbed panes (Plan, Simulation, Tests, Parts, Rover, Background tasks) instead of the old slide-over Details sheet and "Recent chats" drawer. Done 2026-11-04.

- `chat-shell.js` (new IIFE, loaded right after chat-core) owns `SimChat.panes.register({id,label,icon,order,mount,show,hide,refresh})` and `SimChat.shell`. Rover and Background-tasks panes are placeholders that later cards replace by registering the same id.
- `SimChat.sheet.open(tab)` still works and maps onto the panes. The single `Viewer3D` is hosted in the Simulation pane via `SimChat.live.setPane(el)` (still one WebGL canvas).
- Projects live only in localStorage (`simlab.chat.projects`) until `simlab/project/` has an API. Sidebar chat list = `GET /api/chat/list` (read-only, added to chat_router.py).
- Breakpoints: sidebar inline >= 900 px, panes inline >= 1200 px, drawers below. Browser pane note: the preview port 8000 may be taken by another session, so `demo` (8001) is the usable launch config.

**Why:** the user wanted the look of the Claude desktop app for the judges' demo; Phase C cards (Requirements, Equipment, Rover...) must plug into the pane registry.
**How to apply:** new flow cards that need a right-hand view register a pane instead of opening a modal; keep honesty wording and one shared viewer. See [[project-simlab-generalisation]].
