# Drop-in skills

Any `.md` file in this folder is loaded into the agent's system prompt at
boot (8KB per file, 64KB total budget; oversized files are skipped and
reported in the boot journal). Start a file with `<!-- skip` to keep it
dormant. No manifest, no code — drop and reboot.
