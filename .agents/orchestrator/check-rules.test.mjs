import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), 'check-rules.mjs')

function repo(files) {
  const dir = mkdtempSync(join(tmpdir(), 'check-rules-'))
  const g = (...a) => execFileSync('git', ['-C', dir, ...a], { stdio: 'pipe' })
  g('init', '-q', '-b', 'main')
  g('config', 'user.email', 't@t')
  g('config', 'user.name', 't')
  write(dir, files)
  g('add', '-A')
  g('commit', '-qm', 'base')
  return { dir, g }
}
function write(dir, files) {
  for (const [f, body] of Object.entries(files)) {
    mkdirSync(dirname(join(dir, f)), { recursive: true })
    writeFileSync(join(dir, f), body)
  }
}
const run = (dir, ...a) => spawnSync('node', [SCRIPT, ...a], { cwd: dir, encoding: 'utf8' })

const BASE = {
  'package.json': '{"scripts":{"build":"true"}}',
  'src/a.ts': 'one\ntwo\n',
  'pkg/web/package.json': '{"scripts":{"start":"true"}}',
  'pkg/web/scripts/seed.mjs': '',
  'CLAUDE.md': 'Run `bun run build` and `npm run start`. See `src/a.ts:2` and src/a.ts.\nNot ours: owner/repo, read/write, `TASK.md`, `~/x/y`.\n',
  'pkg/web/CLAUDE.md': 'Seed with `scripts/seed.mjs`.\n',
}

test('a clean repo has no warnings and exits 0', () => {
  const { dir } = repo(BASE)
  const r = run(dir)
  assert.equal(r.status, 0)
  assert.match(r.stdout, /2 instruction files/)
  assert.match(r.stdout, /0 warnings/)
})

test('flags missing paths, lines past the end, missing scripts and claude-mem-only files', () => {
  const { dir } = repo({
    ...BASE,
    'CLAUDE.md': 'Use `src/gone.ts`, `src/a.ts:99` and `bun run nope`.\n',
    'src/CLAUDE.md': '<claude-mem-context>\n# Recent Activity\n</claude-mem-context>\n',
  })
  const out = run(dir).stdout
  assert.match(out, /WARN CLAUDE\.md:1 {2}missing path `src\/gone\.ts`/)
  assert.match(out, /`src\/a\.ts:99` is past the end of the file/)
  assert.match(out, /package\.json has no script `nope`/)
  assert.match(out, /WARN src\/CLAUDE\.md:1 {2}holds only auto-generated claude-mem notes/)
  assert.match(out, /4 warnings/)
})

test('--base reports only new warnings, and --strict fails only on those', () => {
  const { dir, g } = repo({ ...BASE, 'CLAUDE.md': 'Old: `src/old.ts`.\n' })
  g('checkout', '-qb', 'feat')
  write(dir, { 'CLAUDE.md': 'Old: `src/old.ts`.\nNew: `src/new.ts`.\n' })
  const r = run(dir, '--base', 'main', '--strict')
  assert.equal(r.status, 1)
  assert.match(r.stdout, /^OLD WARN CLAUDE\.md:1 {2}missing path `src\/old\.ts`$/m)
  assert.match(r.stdout, /^NEW WARN CLAUDE\.md:2 {2}missing path `src\/new\.ts`$/m)
  assert.match(r.stdout, /2 warnings, 1 new vs main/)
  assert.equal(run(dir, '--base', 'main').status, 0)
})

test('an unknown base counts every warning as new', () => {
  const { dir } = repo({ ...BASE, 'CLAUDE.md': '`src/old.ts`\n' })
  const r = run(dir, '--base', 'origin/nope')
  assert.equal(r.status, 0)
  assert.match(r.stdout, /base origin\/nope not found/)
})
