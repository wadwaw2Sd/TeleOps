'use strict';
/** 首次运行时把配置和内置插件铺到用户数据目录。 */
const path = require('node:path');
const fs = require('node:fs');
const P = require('./paths');

function copyIfAbsent(src, dst) {
  if (!fs.existsSync(src) || fs.existsSync(dst)) return false;
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.copyFileSync(src, dst);
  return true;
}

/** 递归复制目录，已存在的文件不覆盖（用户改过的插件不会被升级冲掉）。 */
function mergeDir(src, dst, skip = new Set(['__pycache__', '.git'])) {
  if (!fs.existsSync(src)) return 0;
  let n = 0;
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (skip.has(entry.name) || entry.name.endsWith('.pyc')) continue;
    const from = path.join(src, entry.name);
    const to = path.join(dst, entry.name);
    if (entry.isDirectory()) n += mergeDir(from, to, skip);
    else if (copyIfAbsent(from, to)) n += 1;
  }
  return n;
}

/**
 * 准备用户数据目录。开发模式下 home 就是仓库根，什么都不用做。
 * 返回一段可以显示给用户的说明（没做事就返回 null）。
 */
function prepareHome(onLog) {
  P.ensureDirs();
  if (path.resolve(P.home) === path.resolve(P.pySrc)) return null;

  const created = [];
  if (copyIfAbsent(path.join(P.pySrc, 'config.example.yaml'), P.configFile)) {
    created.push('config.yaml');
  }
  const plugins = mergeDir(path.join(P.pySrc, 'plugins'), path.join(P.home, 'plugins'));
  if (plugins) created.push(`${plugins} 个插件文件`);

  if (created.length && onLog) onLog(`已初始化 ${created.join(' / ')} → ${P.home}`);
  return created.length ? created.join(' / ') : null;
}

/** 从 config.yaml 里读端口（不引第三方 YAML 库，够用就行）。 */
function configuredPort(fallback = 8800) {
  try {
    const text = fs.readFileSync(P.configFile, 'utf8');
    const m = text.match(/^\s*server:\s*$([\s\S]*?)(?=^\S|\Z)/m);
    const block = m ? m[1] : text;
    const p = block.match(/^\s+port:\s*(\d+)/m);
    if (p) return Number(p[1]);
  } catch {
    /* 没有配置文件就用默认端口 */
  }
  return fallback;
}

module.exports = { prepareHome, configuredPort };
