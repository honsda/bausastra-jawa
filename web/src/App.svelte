<script>
  import { onMount } from 'svelte';
  import { toJavanese } from 'carakanjs';

  let route = { name: 'home' };
  let letters = [];
  let stats = { entries: 0, relations: 0 };
  let q = '';
  let results = null;
  let letterData = null;
  let wordData = null;
  let loading = false;

  async function get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }

  function parseHash() {
    const h = location.hash || '#/';
    let m;
    if ((m = h.match(/^#\/letter\/([A-Za-z])(?:\?page=(\d+))?/))) {
      route = { name: 'letter', letter: m[1].toUpperCase(), page: parseInt(m[2] || '1') };
      results = null; // a letter category replaces search results
    } else if ((m = h.match(/^#\/word\/(\d+)/))) {
      route = { name: 'word', id: m[1] };
      results = null;
    } else {
      route = { name: 'home' };
    }
    load();
  }

  async function load() {
    loading = true;
    letterData = null; wordData = null;
    try {
      if (route.name === 'letter') {
        letterData = await get(`/api/letter/${route.letter}?page=${route.page}`);
      } else if (route.name === 'word') {
        wordData = await get(`/api/word/${route.id}`);
      }
    } catch (e) {
      letterData = { error: true }; wordData = { error: true };
    }
    loading = false;
  }

  async function search() {
    // searching always cancels any chosen letter/word view and queries fresh
    if (route.name !== 'home') {
      route = { name: 'home' };
      letterData = null; wordData = null;
      if ((location.hash || '#/') !== '#/') location.hash = '#/';
    }
    if (!q.trim()) { results = null; return; }
    loading = true;
    try {
      results = await get(`/api/search?q=${encodeURIComponent(q.trim())}`);
    } catch (e) {
      results = [];
    }
    loading = false;
  }

  function meta(item) {
    return [item.lang, item.speech_level, item.source].filter(Boolean).join(' / ');
  }

  function fmt(n) {
    return (n || 0).toLocaleString('id-ID');
  }

  function aksaraOf(w) {
    if (!w) return '';
    if (w.aksara_jawa) return w.aksara_jawa; // official spelling wins when present
    try {
      return toJavanese(w.headword, { useAccents: true });
    } catch (e) {
      return '';
    }
  }

  onMount(async () => {
    window.addEventListener('hashchange', parseHash);
    try {
      [letters, stats] = await Promise.all([get('/api/letters'), get('/api/stats')]);
    } catch (e) { /* API not running yet */ }
    parseHash();
  });
</script>

<header>
  <h1><a href="#/">ꦧꦻꦴꦱꦱ꧀ꦠꦿ Bausastra</a></h1>
  <p class="sub">Kamus Jawa–Indonesia • {fmt(stats.entries)} lema</p>
  <form on:submit|preventDefault={search}>
    <input bind:value={q} placeholder="Contoh: wonten, banyu, mangan…" />
    <button>Cari</button>
  </form>
</header>

<nav class="letters">
  {#each 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('') as ch}
    {@const hit = letters.find((l) => l.letter === ch)}
    {#if hit}
      <a href="#/letter/{ch}" class:active={route.name === 'letter' && route.letter === ch}>{ch} <small>{fmt(hit.count)}</small></a>
    {:else}
      <span class="empty">{ch}</span>
    {/if}
  {/each}
</nav>

<main>
  {#if loading}<p class="muted">Memuat…</p>{/if}

  {#if route.name === 'home'}
    {#if results !== null}
      {#if results.length === 0}
        <p>Hasil untuk <b>{q}</b>: tidak ditemukan.</p>
      {:else}
        <p>{results.length} hasil untuk <b>{q}</b>:</p>
        <ul class="results">
          {#each results as r}
            <li>
              <a href="#/word/{r.id}">{r.headword}</a>
              <span class="muted">{meta(r)}</span>
              {#if r.definition}<p class="def">{r.definition.slice(0, 280)}</p>{/if}
            </li>
          {/each}
        </ul>
      {/if}
    {:else}
      <p class="muted">Cari kata di atas, atau telusuri per huruf awal.</p>
    {/if}
  {/if}

  {#if route.name === 'letter' && letterData && !letterData.error}
    <h2>Huruf “{letterData.letter}”</h2>
    <p class="muted">Hal {letterData.page}/{letterData.pages} • {fmt(letterData.total)} lema</p>
    <ul class="results">
      {#each letterData.items as r}
        <li>
          <a href="#/word/{r.id}">{r.headword}</a>
          <span class="muted">{meta(r)}</span>
          {#if r.definition}<p class="def">{r.definition.slice(0, 280)}</p>{/if}
        </li>
      {/each}
    </ul>
    <div class="pager">
      {#if letterData.page > 1}
        <a href="#/letter/{letterData.letter}?page={letterData.page - 1}">← Sebelumnya</a>
      {/if}
      {#if letterData.page < letterData.pages}
        <a href="#/letter/{letterData.letter}?page={letterData.page + 1}">Berikutnya →</a>
      {/if}
    </div>
  {/if}

  {#if route.name === 'word' && wordData && !wordData.error}
    <h2>{wordData.headword}</h2>
    <p class="muted">{[wordData.lang, wordData.pos, wordData.speech_level, wordData.source].filter(Boolean).join(' • ')}</p>
    <div class="entry-body">
      {#if aksaraOf(wordData)}
        <div class="aksara-side" lang="jv">{aksaraOf(wordData)}</div>
      {/if}
      <div class="defs-side">
        {#if wordData.definitions.length === 0}
          <p><i>Belum ada definisi.</i></p>
        {:else}
          <ol>
            {#each wordData.definitions as d}
              <li><span class="muted">[{d.lang}]</span> {d.text}</li>
            {/each}
          </ol>
        {/if}
        {#if wordData.synonyms.length > 0}
          <p><b>Sinonim/dasanama:</b>
            {#each wordData.synonyms as s, i}
              <a href="#/word/{s.id}">{s.headword}</a>{i < wordData.synonyms.length - 1 ? ', ' : ''}
            {/each}
          </p>
        {/if}
      </div>
    </div>
  {/if}

  {#if (route.name === 'letter' && letterData?.error) || (route.name === 'word' && wordData?.error)}
    <p>Data tidak ditemukan.</p>
  {/if}
</main>

<footer>Sumber: KBJI, sastra-jawa, benih kurasi • ngoko / krama / kawi</footer>

<style>
  :global(body) { font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #222; }
  header a { color: #0f6b4f; text-decoration: none; }
  header a:hover { text-decoration: underline; }
  .sub { color: #666; font-size: 0.85rem; }
  form { display: flex; gap: 0.5rem; margin: 1rem 0; }
  form input { flex: 1; padding: 0.6rem; font-size: 1rem; }
  form button { padding: 0.6rem 1rem; font-size: 1rem; background: #0f6b4f; color: #fff; border: 0; cursor: pointer; }
  .letters { display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 1rem 0; }
  .letters a { padding: 0.3rem 0.6rem; border: 1px solid #ccc; border-radius: 6px; text-decoration: none; color: #0f6b4f; }
  .letters a small { color: #888; }
  .letters a.active { background: #0f6b4f; color: #fff; border-color: #0f6b4f; }
  .letters a.active small { color: #cfe8dd; }
  .letters .empty { padding: 0.3rem 0.6rem; opacity: 0.35; }
  ul.results { list-style: none; padding: 0; }
  ul.results li { padding: 0.5rem 0; border-bottom: 1px solid #eee; }
  ul.results a { font-weight: 600; color: #111; text-decoration: none; }
  ul.results a:hover { text-decoration: underline; }
  .muted { color: #666; font-size: 0.85rem; }
  .def { margin: 0.2rem 0; }
  .entry-body { display: flex; gap: 1.25rem; align-items: flex-start; }
  .aksara-side { font-size: 2rem; line-height: 1.6; min-width: 7rem; max-width: 13rem; color: #0f6b4f; overflow-wrap: anywhere; }
  .defs-side { flex: 1; min-width: 0; }
  .defs-side ol { margin-top: 0.2rem; }
  @media (max-width: 560px) { .entry-body { flex-direction: column; } }
  .pager { margin: 1rem 0; display: flex; gap: 1rem; }
  footer { margin-top: 2rem; color: #888; font-size: 0.85rem; }
</style>
