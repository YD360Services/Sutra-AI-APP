const fs = require('fs');
const path = require('path');

const dir = __dirname;
const filesToProcess = ['styles.css', 'renderer.js', 'index.html', 'stealth-popup.html'];

const replacements = [
  { regex: /#8b5cf6/gi, replace: '#14b8a6' },
  { regex: /#a78bfa/gi, replace: '#2dd4bf' },
  { regex: /#7c3aed/gi, replace: '#0d9488' },
  { regex: /#c084fc/gi, replace: '#5eead4' },
  { regex: /139,\s*92,\s*246/g, replace: '20, 184, 166' },
  { regex: /167,\s*139,\s*250/g, replace: '45, 212, 191' },
  { regex: /124,\s*58,\s*237/g, replace: '13, 148, 136' },
  { regex: /192,\s*132,\s*252/g, replace: '94, 234, 212' }
];

for (const file of filesToProcess) {
  const filePath = path.join(dir, file);
  if (fs.existsSync(filePath)) {
    let content = fs.readFileSync(filePath, 'utf8');
    let changed = false;
    for (const r of replacements) {
      if (r.regex.test(content)) {
        content = content.replace(r.regex, r.replace);
        changed = true;
      }
    }
    if (changed) {
      fs.writeFileSync(filePath, content, 'utf8');
      console.log(`Updated ${file}`);
    }
  }
}
