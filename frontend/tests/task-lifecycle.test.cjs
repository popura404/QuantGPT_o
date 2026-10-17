const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function loadTs(relative, mocks = {}, globals = {}) {
  const filename = path.resolve(__dirname, '..', relative);
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(source, {
    exports, console, AbortController, Headers, Error,
    require: (name) => {
      if (name in mocks) return mocks[name];
      return loadTs(path.relative(path.resolve(__dirname, '..'), path.resolve(path.dirname(filename), `${name}.ts`)), mocks, globals);
    },
    ...globals,
  }, { filename });
  return exports;
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const response = (body, status = 200) => ({ status, ok: status < 400, json: async () => body });

function streamHarness(fetch) {
  let id = 0;
  const timers = new Map();
  const sources = [];
  class EventSource {
    constructor() { this.listeners = {}; this.closed = false; sources.push(this); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    close() { this.closed = true; }
    emit(name, data) { this.listeners[name]?.({ data: JSON.stringify(data) }); }
  }
  const api = loadTs('src/api/client.ts', {}, {
    localStorage: { getItem: () => null }, fetch, EventSource,
    setTimeout: callback => { timers.set(++id, callback); return id; },
    clearTimeout: key => timers.delete(key),
  });
  return {
    api, timers, sources,
    tick() {
      const [key, callback] = timers.entries().next().value;
      timers.delete(key);
      callback();
    },
  };
}

test('fallback waits for a slow request before scheduling another, then stops at terminal state', async () => {
  const first = deferred();
  let reads = 0;
  const h = streamHarness((url) => url.endsWith('/sse-ticket')
    ? Promise.resolve(response({ detail: 'unavailable' }, 503))
    : ++reads === 1 ? first.promise : Promise.resolve(response({ status: 'completed' })));
  const updates = [];
  let done = 0;
  h.api.streamTask('task', task => updates.push(task.status), () => done++);
  await settle();
  h.tick();
  await settle();
  assert.equal(reads, 1);
  assert.equal(h.timers.size, 0, 'a slow request must not leave an overlapping timer');
  first.resolve(response({ status: 'running' }));
  await settle();
  assert.equal(h.timers.size, 1);
  h.tick();
  await settle();
  assert.deepEqual(updates, ['running', 'completed']);
  assert.equal(done, 1);
  assert.equal(h.timers.size, 0);
});

test('closing fallback aborts the request and ignores a late response even when fetch ignores abort', async () => {
  const pending = deferred();
  let signal;
  const h = streamHarness((url, options) => {
    if (url.endsWith('/sse-ticket')) return Promise.resolve(response({ detail: 'unavailable' }, 503));
    signal = options.signal;
    return pending.promise;
  });
  const updates = [];
  let done = 0;
  const close = h.api.streamTask('task', task => updates.push(task), () => done++);
  await settle();
  h.tick();
  close();
  assert.equal(signal.aborted, true);
  pending.resolve(response({ status: 'completed' }));
  await settle();
  assert.equal(updates.length, 0);
  assert.equal(done, 0);
  assert.equal(h.timers.size, 0);
});

test('closing SSE removes retry timers and late events cannot publish updates', async () => {
  const h = streamHarness(async () => response({ ticket: 'test-ticket' }));
  const updates = [];
  const close = h.api.streamTask('task', task => updates.push(task), () => {});
  await settle();
  const source = h.sources[0];
  source.emit('error');
  assert.equal(h.timers.size, 1);
  close();
  source.emit('update', { status: 'running' });
  assert.equal(h.timers.size, 0);
  assert.equal(source.closed, true);
  assert.equal(updates.length, 0);
});

test('terminal SSE update closes immediately and duplicate terminal events finish only once', async () => {
  const h = streamHarness(async () => response({ ticket: 'test-ticket' }));
  let updates = 0, done = 0;
  h.api.streamTask('task', () => updates++, () => done++);
  await settle();
  const source = h.sources[0];
  source.emit('update', { status: 'completed' });
  source.emit('update', { status: 'running' });
  source.emit('done');
  assert.equal(updates, 1);
  assert.equal(done, 1);
  assert.equal(source.closed, true);
});

// An injected hook scheduler exercises the hook's asynchronous public actions
// without a browser. React rendering/DOM behavior remains covered by Playwright.
function hookHarness(relative, exportName, mocks, initialArgs, globals = {}) {
  const slots = [];
  let cursor = 0, effects = [], args = initialArgs, result;
  const same = (left, right) => left?.length === right?.length && left.every((item, index) => Object.is(item, right[index]));
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, value => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { current: initial };
      return slots[index];
    },
    useCallback(callback, deps) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].deps, deps)) slots[index] = { value: callback, deps };
      return slots[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].deps, deps)) {
        const previous = slots[index];
        slots[index] = { deps, cleanup: previous?.cleanup };
        effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); });
      }
    },
  };
  const hook = loadTs(relative, { ...mocks, react }, globals)[exportName];
  const harness = {
    render(nextArgs = args) {
      args = nextArgs;
      cursor = 0;
      effects = [];
      result = hook(...args);
      effects.forEach(effect => effect());
      return result;
    },
    unmount() { slots.forEach(slot => slot.cleanup?.()); },
  };
  harness.render();
  return harness;
}

function backtestHarness(overrides = {}, onComplete = () => {}) {
  const streams = [];
  const api = {
    submitBacktest: async () => ({ task_id: 'single' }),
    submitIteration: async () => ({ task_id: 'iteration' }),
    cancelTask: async () => {},
    getTask: async () => ({ task_id: 'single', status: 'cancelled' }),
    streamTask: (taskId, update, done, error) => {
      const entry = { taskId, update, done, error, closed: false };
      streams.push(entry);
      return () => { entry.closed = true; };
    },
    ...overrides,
  };
  const h = hookHarness('src/hooks/useBacktest.ts', 'useBacktest', {
    '../api/client': api,
    '../api/composite': { submitCompositeBacktest: overrides.submitCompositeBacktest ?? (async () => ({ task_id: 'composite' })) },
  }, [onComplete, 'session-a']);
  return { ...h, streams, onComplete };
}

test('switching sessions closes the stream and rejects late updates and completion callbacks', async () => {
  let completed = 0;
  const h = backtestHarness({}, () => completed++);
  await h.render().submit({ prompt: 'rank(close)' });
  assert.equal(h.render().isLoading, true);
  const old = h.streams[0];
  h.render([h.onComplete, 'session-b']);
  old.update({ task_id: 'single', status: 'completed' });
  old.done();
  const state = h.render();
  assert.equal(old.closed, true);
  assert.equal(state.activeTask, null);
  assert.equal(state.isLoading, false);
  assert.equal(completed, 0);
});

test('late submission cannot establish a stream after a session switch or unmount', async () => {
  for (const unmount of [false, true]) {
    const pending = deferred();
    const h = backtestHarness({ submitBacktest: () => pending.promise });
    const submitting = h.render().submit({ prompt: 'rank(close)' });
    if (unmount) h.unmount();
    else h.render([h.onComplete, 'session-b']);
    pending.resolve({ task_id: 'late' });
    assert.equal(await submitting, false);
    assert.equal(h.streams.length, 0);
  }
});

test('combination submission failure is returned to its form and success shares lifecycle cleanup', async () => {
  const failure = new Error('Invalid factor');
  const failed = backtestHarness({ submitCompositeBacktest: async () => { throw failure; } });
  await assert.rejects(failed.render().submitComposite({ factors: [] }), /Invalid factor/);
  assert.equal(failed.render().isLoading, false);
  assert.equal(failed.render().activeTask, null);

  const h = backtestHarness();
  await h.render().submit({ prompt: 'rank(close)' });
  assert.equal(await h.render().submitComposite({ factors: [] }), true);
  assert.equal(h.streams[0].closed, true);
  const composite = h.streams[1];
  assert.equal(h.render().activeTask.task_type, 'composite');
  assert.equal(h.render().isLoading, true);
  h.unmount();
  composite.update({ task_id: 'composite', status: 'completed' });
  assert.equal(composite.closed, true);
});

test('selecting history prevents an old stream and cancellation response from replacing the selection', async () => {
  const pending = deferred();
  const h = backtestHarness({ cancelTask: () => pending.promise });
  await h.render().submit({ prompt: 'rank(close)' });
  const cancel = h.render().cancel();
  h.render().selectTask({ task_id: 'history', status: 'completed' });
  h.streams[0].update({ task_id: 'single', status: 'completed' });
  pending.resolve();
  await cancel;
  assert.equal(h.render().activeTask.task_id, 'history');
  assert.equal(h.streams[0].closed, true);
});

test('task history ignores responses for a previous session', async () => {
  const old = deferred(), current = deferred();
  const h = hookHarness('src/hooks/useTaskHistory.ts', 'useTaskHistory', {
    '../api/client': { fetchTasks: (_page, _size, session) => session === 'a' ? old.promise : current.promise },
  }, ['a']);
  h.render(['b']);
  current.resolve({ tasks: [{ task_id: 'b-task' }] });
  await settle();
  old.resolve({ tasks: [{ task_id: 'a-task' }] });
  await settle();
  assert.equal(h.render().tasks[0].task_id, 'b-task');
});

test('selecting a task summary loads full results and aborts obsolete detail requests', async () => {
  const requests = [];
  const h = backtestHarness({ getTask: (id, signal) => {
    const waiting = deferred();
    requests.push({ id, signal, ...waiting });
    return waiting.promise;
  } });
  h.render().selectTask({ task_id: 'first', status: 'completed', summary_only: true });
  assert.equal(h.render().isLoading, true);
  assert.equal(requests[0].id, 'first');
  h.render().selectTask({ task_id: 'second', status: 'completed', summary_only: true });
  assert.equal(requests[0].signal.aborted, true);
  requests[1].resolve({ task_id: 'second', status: 'completed', result: { nav_series: [1, 2] } });
  await settle();
  requests[0].resolve({ task_id: 'first', status: 'completed' });
  await settle();
  const state = h.render();
  assert.equal(state.activeTask.task_id, 'second');
  assert.equal(state.activeTask.result.nav_series.length, 2);
  assert.equal(state.activeTask.summary_only, undefined);
  assert.equal(state.isLoading, false);
});

test('drawer detail loader clears selection on close and ignores a late response', async () => {
  const waiting = deferred();
  let signal;
  const source = { task_id: 'summary', summary_only: true, status: 'completed' };
  const h = hookHarness('src/hooks/useTaskDetail.ts', 'useTaskDetail', {
    '../api/client': { getTask: (_id, value) => { signal = value; return waiting.promise; } },
  }, [source]);
  assert.equal(h.render().loading, true);
  h.render([null]);
  assert.equal(signal.aborted, true);
  waiting.resolve({ task_id: 'summary', status: 'completed' });
  await settle();
  assert.equal(h.render().task, null);
  assert.equal(h.render().loading, false);
});

test('drawer detail loader exposes fetch failures and passes full tasks without fetching', async () => {
  const failure = new Error('Task no longer exists');
  let reads = 0;
  const h = hookHarness('src/hooks/useTaskDetail.ts', 'useTaskDetail', {
    '../api/client': { getTask: async () => { reads++; throw failure; } },
  }, [{ task_id: 'summary', summary_only: true, status: 'completed' }]);
  await settle();
  assert.equal(h.render().loading, false);
  assert.match(h.render().error, /Task no longer exists/);
  const complete = { task_id: 'complete', status: 'completed', result: { nav_series: [1, 2] } };
  assert.equal(h.render([complete]).task, complete);
  assert.equal(reads, 1);
});

function validationHarness(overrides = {}, stored = {}) {
  const storage = new Map(Object.entries(stored));
  const streams = [];
  const profile = { evaluation_id: 'evaluation', profile: 'local_factor', allowed: false, blockers: [], checks: {} };
  const h = hookHarness('src/hooks/useResearchValidation.ts', 'useResearchValidation', {
    '../api/client': {
      getTask: overrides.getTask ?? (async () => ({ task_id: 'validation', status: 'queued' })),
      streamTask: (_taskId, update, done, error) => {
        const stream = { update, done, error, closed: false };
        streams.push(stream);
        return () => { stream.closed = true; };
      },
    },
    '../api/research': {
      getResearchValidation: overrides.getResearchValidation ?? (async () => profile),
      submitResearchValidation: overrides.submitResearchValidation ?? (async () => ({ task_id: 'validation', status: 'queued' })),
    },
  }, ['project', 'evaluation'], {
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    crypto: { randomUUID: () => 'validation-test-key' },
  });
  return { ...h, streams, storage };
}

test('offline validation completion refreshes server evidence while preserving external blockers', async () => {
  let reads = 0;
  const h = validationHarness({ getResearchValidation: async () => ({
    evaluation_id: 'evaluation', allowed: false, profile: 'local_factor',
    checks: ++reads > 1 ? { simulation: { status: 'passed' } } : {},
    blockers: ['EXTERNAL_DATA_ACCEPTANCE_REQUIRED'], offline_validation: reads > 1 ? { artifact_id: 'immutable-report' } : null,
  }) });
  await settle();
  assert.equal(h.render().busy, false);
  await h.render().start();
  assert.equal(h.render().busy, true);
  h.streams[0].update({ task_id: 'validation', status: 'completed' });
  await settle();
  const state = h.render();
  assert.equal(state.busy, false);
  assert.equal(state.profile.offline_validation.artifact_id, 'immutable-report');
  assert.equal(state.profile.allowed, false);
  assert.equal(state.profile.blockers[0], 'EXTERNAL_DATA_ACCEPTANCE_REQUIRED');
  assert.equal(h.streams[0].closed, true);
  assert.equal(h.storage.has('quantgpt_validation_task_project_evaluation'), false);
});

test('offline validation ignores an old project task and resumes persisted work after remount', async () => {
  const h = validationHarness({}, { quantgpt_validation_task_project_evaluation: 'validation' });
  await settle();
  assert.equal(h.streams.length, 1);
  assert.equal(h.render().busy, true);
  h.render(['other-project', 'other-evaluation']);
  await settle();
  h.streams[0].update({ task_id: 'validation', status: 'completed' });
  await settle();
  assert.equal(h.streams[0].closed, true);
  assert.equal(h.render().task, null);
  assert.equal(h.render().busy, false);
});

test('offline validation reuses the submission key after a lost response', async () => {
  const keys = [];
  const h = validationHarness({ submitResearchValidation: async (_project, _evaluation, _selection, key) => {
    keys.push(key);
    if (keys.length === 1) throw new Error('Connection interrupted');
    return { task_id: 'validation', status: 'queued' };
  } });
  await settle();
  await h.render().start();
  assert.match(h.render().error, /Connection interrupted/);
  await h.render().start();
  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
  assert.equal(h.streams.length, 1);
});

test('selection linkage requires one matching frozen local candidate before the final window', () => {
  const { canValidateLocally, matchingSelectionEvaluation } = loadTs('src/utils/researchValidation.ts');
  const candidate = (id, phase, start, end, fees = 5) => ({
    evaluation_id: id, project_id: 'p', definition_hash: 'definition', backend: 'local',
    summary: { performance_observed: true }, frozen_config: { config: {
      fees, window: { phase, start_session: start, end_session: end, label_horizon_sessions: 5 },
    } },
  });
  const final = candidate('final', 'final', '2024-07-01', '2024-12-31');
  const selection = candidate('selection', 'selection', '2024-01-01', '2024-06-28');
  assert.equal(matchingSelectionEvaluation(final, [selection]), 'selection');
  assert.equal(matchingSelectionEvaluation(final, [candidate('wrong-fees', 'selection', '2024-01-01', '2024-06-28', 10)]), undefined);
  assert.equal(matchingSelectionEvaluation(final, [candidate('overlap', 'selection', '2024-01-01', '2024-07-01')]), undefined);
  assert.equal(matchingSelectionEvaluation(final, [selection, { ...selection, evaluation_id: 'ambiguous' }]), undefined);
  assert.equal(canValidateLocally({ ...selection, backend: 'wq' }), false);
  assert.equal(canValidateLocally({ ...selection, summary: { performance_observed: false } }), false);
});

test('validation API sends only server references and reads the local factor profile', async () => {
  const requests = [];
  const api = loadTs('src/api/research.ts', { './client': {
    BASE: '', parseError: async () => 'error', authFetch: async (url, options) => {
      requests.push({ url, options });
      return response({ task_id: 'validation', status: 'queued', poll_url: '/api/v1/tasks/validation' });
    },
  } });
  await api.submitResearchValidation('p/scope', 'e/value', 'selection', 'retry-key');
  assert.equal(requests[0].url, '/api/v1/research/projects/p%2Fscope/evaluations/e%2Fvalue/validation-runs');
  assert.deepEqual(JSON.parse(requests[0].options.body), { selection_evaluation_id: 'selection', idempotency_key: 'retry-key' });
  await api.getResearchValidation('project', 'evaluation');
  assert.equal(requests[1].url, '/api/v1/research/projects/project/evaluations/evaluation/validation?profile=local_factor');
});
