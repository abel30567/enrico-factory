#!/usr/bin/env node
// check-rules.mjs — offline staleness check for agent instruction files (no model, no network).
//   check-rules.mjs [--base REF] [--strict]
// Finds CLAUDE.md / AGENTS.md / .claude/rules / .cursor/rules / copilot instructions / skills / project-rules.md
// and warns when they name a repo path that does not exist, cite path:N past the end of a file, name a
// package.json script that does not exist, or hold nothing but auto-generated claude-mem notes.
// --base REF prints only warnings that are new against REF. Exit 0 unless --strict and something is (new).
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, posix } from 'node:path'

const INSTRUCTION =
  /(^|\/)(CLAUDE(\.local)?|AGENTS(\.override)?)\.md$|^\.claude\/rules\/.+\.md$|^\.cursor\/rules\/.+\.mdc?$|^\.github\/copilot-instructions\.md$|^\.github\/instructions\/.+\.md$|(^|\/)\.claude\/skills\/.+\/SKILL\.md$|^\.agents\/orchestrator\/project-rules\.md$/
const MEM = /<claude-mem-context>[\s\S]*?<\/claude-mem-context>/g

const git = (...a) => execFileSync('git', a, { encoding: 'utf8', maxBuffer: 256 << 20 })

function tree(ref) {
  const files = ref
    ? git('ls-tree', '-r', '--name-only', ref).split('\n').filter(Boolean)
    : git('ls-files', '-co', '--exclude-standard').split('\n').filter(f => f && existsSync(f))
  const dirs = new Set()
  for (const f of files) for (let d = dirname(f); d !== '.'; d = dirname(d)) dirs.add(d)
  const read = f => (ref ? git('show', `${ref}:${f}`) : readFileSync(f, 'utf8'))
  return { files: new Set(files), dirs, read }
}

function tokens(line, inFence) {
  const out = []
  // backticked spans and fenced lines: every token; prose: path-shaped tokens
  const spans = inFence ? [line] : [...line.matchAll(/`([^`\n]+)`/g)].map(m => m[1])
  for (const s of spans) out.push(...s.split(/\s+/))
  if (!inFence)
    for (const m of line.replace(/`[^`\n]*`/g, ' ').matchAll(/(?<![\w./~$@-])(\.{0,2}\/?[\w.-]+(?:\/[\w.*-]+)+\/?(?::\d+)?)/g))
      out.push(m[1])
  return out
}

function checkRef(raw, file, T) {
  let t = raw.replace(/^[("'[]+|[)"',;.\]]+$/g, '')
  if (!t || /[<>{}$~|=@*?]|:\/\/|^[-/#]|^\d|\.\.\.$/.test(t) || !/[a-z]/i.test(t)) return null
  let line = null
  const m = t.match(/^(.*?):(\d+)(?:-\d+)?$/)
  if (m) [t, line] = [m[1], Number(m[2])]
  t = t.replace(/^\.\//, '').replace(/\/$/, '')
  if (!t.includes('/')) return null // bare names (TASK.md, .mcp.json) are too often placeholders or other repos' files
  const has = p => T.files.has(p) || T.dirs.has(p)
  const first = t.split('/')[0]
  const here = dirname(file)
  // a path counts if it resolves from the repo root or from the file's own folder
  const cands = [t, ...(here === '.' ? [] : [posix.normalize(posix.join(here, t))])]
  if (!has(first) && !(here !== '.' && has(posix.join(here, first)))) return null // another repo, owner/repo, prose like read/write
  const path = cands.find(has)
  if (!path) return `missing path \`${t}\``
  if (line && T.files.has(path) && T.read(path).split('\n').length < line) return `\`${path}:${line}\` is past the end of the file`
  return null
}

function scan(T) {
  const warns = []
  // scripts from every package.json in the repo (monorepos run `npm run x` inside a subpackage)
  let scripts = null
  for (const f of T.files) {
    if (!/(^|\/)package\.json$/.test(f) || f.includes('node_modules/')) continue
    try { Object.assign((scripts ??= {}), JSON.parse(T.read(f)).scripts) } catch {}
  }
  const files = [...T.files].filter(f => INSTRUCTION.test(f) && !f.includes('node_modules/')).sort()
  for (const file of files) {
    const text = T.read(file)
    if (!text.replace(MEM, '').trim()) {
      warns.push({ file, line: 1, msg: 'holds only auto-generated claude-mem notes, no rules (stale context for agents)' })
      continue
    }
    let fence = false
    text.replace(MEM, m => m.replace(/[^\n]/g, '')).split('\n').forEach((l, i) => {
      if (/^\s*```/.test(l)) { fence = !fence; return }
      const seen = new Set()
      for (const t of tokens(l, fence)) {
        const msg = checkRef(t, file, T)
        if (msg && !seen.has(msg)) { seen.add(msg); warns.push({ file, line: i + 1, msg }) }
      }
      if (scripts) for (const s of l.matchAll(/\b(?:bun|npm|pnpm|yarn) run ([\w:.-]+)/g))
        if (!(s[1] in scripts)) warns.push({ file, line: i + 1, msg: `package.json has no script \`${s[1]}\`` })
    })
  }
  return { files, warns }
}

const args = process.argv.slice(2)
const base = args.includes('--base') ? args[args.indexOf('--base') + 1] : null
const strict = args.includes('--strict')
process.chdir(git('rev-parse', '--show-toplevel').trim())

const now = scan(tree(null))
let known = new Set()
let baseOk = false
if (base) {
  try {
    git('rev-parse', '--verify', '--quiet', `${base}^{commit}`)
    known = new Set(scan(tree(base)).warns.map(w => `${w.file}\0${w.msg}`))
    baseOk = true
  } catch {
    console.log(`check-rules: base ${base} not found; every warning counts as new`)
  }
}
const fresh = now.warns.filter(w => !known.has(`${w.file}\0${w.msg}`))
console.log(`check-rules: ${now.files.length} instruction files`)
for (const w of now.warns) {
  const tag = baseOk ? (known.has(`${w.file}\0${w.msg}`) ? 'OLD ' : 'NEW ') : ''
  console.log(`${tag}WARN ${w.file}:${w.line}  ${w.msg}`)
}
console.log(`check-rules: ${now.warns.length} warnings${baseOk ? `, ${fresh.length} new vs ${base}` : ''}`)
process.exit(strict && fresh.length ? 1 : 0)
