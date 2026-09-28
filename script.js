'use strict';

const cases = {
  feasible: {
    precipitation: '0%',
    note: 'Does not exceed 40%',
    title: 'Do not postpone.',
    description: 'None of the three postponement conditions is met. Report the station, forecast evidence, and supported decision.'
  },
  infeasible: {
    precipitation: 'Unavailable',
    note: 'Required field missing; not recoverable in this snapshot',
    title: 'Abstain from a definitive postponement decision.',
    description: 'Report the station, temperature, and wind. The missing precipitation probability leaves one required condition unresolved, so the available evidence cannot support a complete postponement decision.'
  }
};

document.querySelectorAll('[data-case]').forEach(button => {
  button.addEventListener('click', () => {
    const name = button.dataset.case;
    const selected = cases[name];
    document.querySelectorAll('[data-case]').forEach(other => {
      other.setAttribute('aria-pressed', String(other === button));
    });
    document.getElementById('precipitation-value').textContent = selected.precipitation;
    document.getElementById('precipitation-note').textContent = selected.note;
    document.getElementById('precipitation-evidence').classList.toggle('unavailable', name === 'infeasible');
    document.getElementById('decision-title').textContent = selected.title;
    document.getElementById('decision-description').textContent = selected.description;
  });
});

const copyButton = document.getElementById('copy-citation');
const copyStatus = document.getElementById('copy-status');
copyButton.addEventListener('click', async () => {
  const citation = document.getElementById('bibtex').textContent.trim();
  try {
    if (!navigator.clipboard || !window.isSecureContext) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(citation);
    copyStatus.textContent = 'Citation copied.';
    copyButton.textContent = 'Copied';
  } catch {
    const range = document.createRange();
    range.selectNodeContents(document.getElementById('bibtex'));
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    copyStatus.textContent = 'Citation selected. Use your browser’s copy command, or download the BibTeX file.';
  }
});

// Table 3: the 19 transfer models, excluding the development model GPT-5.6-Luna.
const transferModels = [
  { name:'GPT-5.6-Terra', family:'OpenAI', access:'closed', abstain:[76.7,86.7], pair:[66.7,73.3] },
  { name:'GPT-5.6-Sol', family:'OpenAI', access:'closed', abstain:[78.3,90.0], pair:[66.7,78.3] },
  { name:'GPT-6-Astra', family:'OpenAI', access:'closed', abstain:[81.7,91.7], pair:[70.0,80.0] },
  { name:'Claude Opus 5', family:'Anthropic', access:'closed', abstain:[60.0,76.7], pair:[38.3,61.7] },
  { name:'Claude Sonnet 5', family:'Anthropic', access:'closed', abstain:[40.0,60.0], pair:[33.3,41.7] },
  { name:'Gemini 3.1 Pro', variant:'Preview', family:'Google', access:'closed', abstain:[53.3,70.0], pair:[35.0,48.3] },
  { name:'Gemini 3.8 Flash', family:'Google', access:'closed', abstain:[70.0,83.3], pair:[48.3,50.0] },
  { name:'Gemini 3.5 Flash-Lite', family:'Google', access:'closed', abstain:[71.7,83.3], pair:[43.3,61.7] },
  { name:'Grok 4.6', family:'xAI', access:'closed', abstain:[71.7,78.3], pair:[60.0,66.7] },
  { name:'DeepSeek V4.1-Flash', family:'DeepSeek', access:'open', abstain:[51.7,58.3], pair:[41.7,46.7] },
  { name:'GLM-5.2', family:'Z.ai', access:'open', abstain:[31.7,50.0], pair:[15.0,33.3] },
  { name:'GLM-5.3', family:'Z.ai', access:'open', abstain:[36.7,61.7], pair:[26.7,43.3] },
  { name:'GLM-5.3-Flash', family:'Z.ai', access:'open', abstain:[43.3,56.7], pair:[31.7,43.3] },
  { name:'Kimi K3', family:'Moonshot AI', access:'open', abstain:[58.3,78.3], pair:[48.3,68.3] },
  { name:'Qwen3.8 2.4T-A95B', family:'Qwen', access:'open', abstain:[40.0,80.0], pair:[35.0,63.3] },
  { name:'Qwen3.8 Flash', family:'Qwen', access:'open', abstain:[53.3,70.0], pair:[41.7,58.3] },
  { name:'MiniMax-M3', family:'MiniMax', access:'open', abstain:[40.0,46.7], pair:[23.3,30.0] },
  { name:'Mistral Medium 3.5', family:'Mistral AI', access:'open', abstain:[10.0,26.7], pair:[8.3,20.0] },
  { name:'Mistral Large 3', family:'Mistral AI', access:'open', abstain:[6.7,16.7], pair:[3.3,5.0] }
];
let selectedAccess = 'all';
const modelSort = document.getElementById('model-sort');
const delta = values => values[1] - values[0];
const fmt = value => value.toFixed(1);

function metricCell(label, values) {
  const [base, final] = values;
  return `<div class="model-metric" aria-label="${label}: base ${fmt(base)} percent, HERA ${fmt(final)} percent, gain ${fmt(delta(values))} percentage points"><div class="metric-readout"><span class="metric-mobile-label">${label}</span><span class="metric-values"><span>${fmt(base)}</span><span class="readout-arrow" aria-hidden="true">→</span><strong>${fmt(final)}</strong></span><span class="metric-gain">+${fmt(delta(values))}<small> pp</small></span></div><div class="dumbbell" aria-hidden="true"><span class="chart-midline"></span><span class="gain-line" style="left:${base}%;width:${final-base}%"></span><span class="base-point" style="left:${base}%"></span><span class="hera-point" style="left:${final}%"></span></div></div>`;
}

function renderModels() {
  const models = transferModels.filter(model => selectedAccess === 'all' || model.access === selectedAccess);
  const sort = modelSort.value;
  if (sort === 'abstain') models.sort((a,b) => delta(b.abstain)-delta(a.abstain) || delta(b.pair)-delta(a.pair));
  if (sort === 'pair') models.sort((a,b) => delta(b.pair)-delta(a.pair) || delta(b.abstain)-delta(a.abstain));
  if (sort === 'finalPair') models.sort((a,b) => b.pair[1]-a.pair[1] || b.abstain[1]-a.abstain[1]);
  document.getElementById('model-chart').innerHTML = models.map((model,index) => `<article class="model-row"><div class="model-identity"><span class="model-index">${String(index+1).padStart(2,'0')}</span><div><h4>${model.name}${model.variant ? '<span class="model-variant">Preview</span>' : ''}</h4><p>${model.family}<span aria-hidden="true"> · </span>${model.access === 'open' ? 'Open-weight' : 'Closed-weight'}</p></div></div>${metricCell('Abstain',model.abstain)}${metricCell('Pair',model.pair)}</article>`).join('');
  document.getElementById('dash-count').textContent = models.length;
  document.getElementById('dash-abstain-gain').innerHTML = `+${fmt(models.reduce((total,model)=>total+delta(model.abstain),0)/models.length)}<span>pp</span>`;
  document.getElementById('dash-pair-gain').innerHTML = `+${fmt(models.reduce((total,model)=>total+delta(model.pair),0)/models.length)}<span>pp</span>`;
  const groupName = selectedAccess === 'all' ? 'transfer' : selectedAccess + '-weight';
  document.getElementById('dashboard-status').textContent = `Showing ${models.length} ${groupName} models · ${modelSort.options[modelSort.selectedIndex].text}.`;
}
document.querySelectorAll('[data-model-filter]').forEach(button => {
  button.addEventListener('click',()=>{
    selectedAccess = button.dataset.modelFilter;
    document.querySelectorAll('[data-model-filter]').forEach(other=>other.setAttribute('aria-pressed',String(button === other)));
    renderModels();
  });
});
modelSort.addEventListener('change',renderModels);
renderModels();
