const { spawn } = require('child_process');
const path = require('path');

console.log("\x1b[35m%s\x1b[0m", "🚀 Initiating Stealth Mode...");

const electronPath = require('electron');
const args = ['.', '--stealth'];

// Spawn the Electron application detached
const child = spawn(electronPath, args, {
  cwd: __dirname,
  detached: true,
  stdio: 'ignore'
});

// Allow the parent process to exit without waiting for the child to terminate
child.unref();

console.log("\x1b[32m%s\x1b[0m", "👁️  Native Stealth Toolbar has been launched directly!");
console.log("\x1b[90m%s\x1b[0m", "Always-on-top, screenshot protection, and Alt+Tab hiding are ACTIVE.");

// Exit parent script cleanly
process.exit(0);
