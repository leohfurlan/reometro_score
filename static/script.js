const initUI = () => {
    // ---------------------------------------------
    // 1. Sidebar toggle
    // ---------------------------------------------
    const sidebarToggle = document.getElementById('sidebarToggle');
    const wrapper = document.getElementById('wrapper');

    if (sidebarToggle && wrapper) {
        sidebarToggle.addEventListener('click', event => {
            event.preventDefault();
            wrapper.classList.toggle('toggled');
            const isClosed = wrapper.classList.contains('toggled');
            localStorage.setItem('sidebar-closed', isClosed);
        });

        const savedState = localStorage.getItem('sidebar-closed');
        if (savedState === 'true') {
            wrapper.classList.add('toggled');
        }
    }

    // ---------------------------------------------
    // 2. Modal de detalhes
    // ---------------------------------------------
    const detailsModal = document.getElementById('detailsModal');

    if (detailsModal) {
        detailsModal.addEventListener('show.bs.modal', event => {
            const button = event.relatedTarget;
            if (!button) return;
            const perfil = button.getAttribute('data-perfil');

            const batch = button.getAttribute('data-batch');
            const material = button.getAttribute('data-material');
            const score = button.getAttribute('data-score');
            const idsAgrupados = button.getAttribute('data-ids');

            const tempReal = (button.getAttribute('data-temp-real') || '').trim();
            const tempPadraoAttr = button.getAttribute('data-temp-padrao');
            const tempPadrao = tempPadraoAttr ? parseFloat(tempPadraoAttr) : NaN;
            const grupo = button.getAttribute('data-grupo');
            const tempoMax = button.getAttribute('data-tempo-max');
            const tempoConfig = button.getAttribute('data-tempo-config');

            const displayBatch = batch && batch !== 'None' ? batch : 'N/A';

            const setSafeText = (selector, val) => {
                const el = detailsModal.querySelector(selector);
                if (el) el.textContent = val;
            };

            setSafeText('#modalBatchDisplay', 'BATCH: ' + displayBatch);
            setSafeText('#modalMaterialDisplay', 'Material: ' + material);
            setSafeText('#modalIdsDisplay', idsAgrupados && idsAgrupados.trim() ? `Ensaios: ${idsAgrupados}` : 'Ensaios: --');

            setSafeText('#modalGrupo', grupo + (perfil ? ` (${perfil})` : ''));

            const tempoDisplay = (() => {
                const medRaw = tempoMax && tempoMax.trim() ? tempoMax.trim() : '';
                const med = medRaw ? `${medRaw} s` : '--';
                if (tempoConfig && tempoConfig.trim()) {
                    if (med !== '--') return `${med} (alvo ${tempoConfig}s)`;
                    return `${tempoConfig}s (alvo)`;
                }
                return med;
            })();
            setSafeText('#modalTempo', tempoDisplay);

            const baseTemp = tempReal || '--';
            const padraoValido = !Number.isNaN(tempPadrao) && tempPadrao > 0;
            const padraoStr = padraoValido ? tempPadrao.toFixed(0) : '';

            let tempDisplay = baseTemp;
            if (padraoValido) {
                if (baseTemp && baseTemp !== '--') {
                    tempDisplay = baseTemp.includes(padraoStr) ? baseTemp : `${baseTemp} / ${padraoStr}`;
                } else {
                    tempDisplay = padraoStr;
                }
            }

            setSafeText('#modalTemp', tempDisplay);

            const scoreElem = detailsModal.querySelector('#modalScoreDisplay');
            if (scoreElem) {
                scoreElem.textContent = score;
                scoreElem.className = parseFloat(score) >= 85 ? 'text-success fw-bold fs-5' : 'text-danger fw-bold fs-5';
            }

            function updateParam(prefix, minId, targetId, maxId, measuredId) {
                const minVal = button.getAttribute(`data-${prefix}-min`);
                const targetVal = button.getAttribute(`data-${prefix}-target`);
                const maxVal = button.getAttribute(`data-${prefix}-max`);
                const measuredVal = button.getAttribute(`data-${prefix}-measured`);
                const status = button.getAttribute(`data-${prefix}-status`);

                setSafeText(`#${minId}`, minVal);
                setSafeText(`#${targetId}`, targetVal);
                setSafeText(`#${maxId}`, maxVal);

                const measuredElem = detailsModal.querySelector(`#${measuredId}`);
                if (measuredElem) {
                    measuredElem.textContent = measuredVal;
                    measuredElem.className = 'fw-bold fs-5';

                    if (status === 'success') {
                        measuredElem.classList.add('text-success');
                        measuredElem.innerHTML += ' <i class="fas fa-check fs-6 ms-1"></i>';
                    } else if (status === 'danger') {
                        measuredElem.classList.add('text-danger');
                        measuredElem.innerHTML += ' <i class="fas fa-exclamation-circle fs-6 ms-1"></i>';
                    } else {
                        measuredElem.classList.add('text-dark');
                    }
                }
            }

            updateParam('ts2', 'ts2Min', 'ts2Target', 'ts2Max', 'ts2Measured');
            updateParam('t90', 't90Min', 't90Target', 't90Max', 't90Measured');
            updateParam('ml', 'mlMin', 'mlTarget', 'mlMax', 'mlMeasured');
        });
    }

    // ---------------------------------------------
    // 3. Gestao de colunas com localStorage
    // ---------------------------------------------
    const COLUMN_PREF_KEY = 'dashboard_column_prefs';

    function saveColumnPreferences() {
        const prefs = {};
        document.querySelectorAll('.col-toggle').forEach(checkbox => {
            const colName = checkbox.getAttribute('data-target');
            prefs[colName] = checkbox.checked;
        });
        localStorage.setItem(COLUMN_PREF_KEY, JSON.stringify(prefs));
    }

    function loadColumnPreferences() {
        const storedPrefs = localStorage.getItem(COLUMN_PREF_KEY);
        if (!storedPrefs) return;

        try {
            const prefs = JSON.parse(storedPrefs);
            document.querySelectorAll('.col-toggle').forEach(checkbox => {
                const colName = checkbox.getAttribute('data-target');
                if (prefs[colName] !== undefined) {
                    checkbox.checked = prefs[colName];
                }
            });
        } catch (err) {
            console.error('Erro ao ler preferencias de colunas', err);
        }
    }

    function applyColumnVisibility() {
        document.querySelectorAll('.col-toggle').forEach(checkbox => {
            const targetClass = checkbox.getAttribute('data-target');
            const isVisible = checkbox.checked;

            document.querySelectorAll('.' + targetClass).forEach(el => {
                el.style.display = isVisible ? '' : 'none';
            });
        });
    }

    loadColumnPreferences();
    applyColumnVisibility();

    document.querySelectorAll('.col-toggle').forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            applyColumnVisibility();
            saveColumnPreferences();
        });
    });

    document.querySelectorAll('.checkbox-stop').forEach(el => {
        el.addEventListener('click', e => e.stopPropagation());
    });

    // ---------------------------------------------
    // 4. Ordenacao com persistencia
    // ---------------------------------------------
    const SORT_PREF_KEY = 'dashboard_sort_pref';

    function saveSortPreference(sort, order) {
        localStorage.setItem(SORT_PREF_KEY, JSON.stringify({ sort, order }));
    }

    function loadSortPreference() {
        const stored = localStorage.getItem(SORT_PREF_KEY);
        if (!stored) return null;
        try {
            return JSON.parse(stored);
        } catch (err) {
            console.error('Erro ao ler preferencia de ordenacao', err);
            return null;
        }
    }

    function attachSortHandlers() {
        document.querySelectorAll('.sort-link').forEach(link => {
            link.addEventListener('click', () => {
                const sort = link.dataset.sort;
                const order = link.dataset.order;
                if (sort && order) {
                    saveSortPreference(sort, order);
                }
            });
        });
    }

    function captureSortFromTable() {
        const table = document.getElementById('mainTable');
        if (!table) return;
        const currentSort = table.dataset.currentSort;
        const currentOrder = table.dataset.currentOrder;
        if (currentSort && currentOrder) {
            saveSortPreference(currentSort, currentOrder);
        }
    }

    function ensureSortPreferenceApplied() {
        const table = document.getElementById('mainTable');
        const saved = loadSortPreference();
        if (!table || !saved) return;

        const currentSort = table.dataset.currentSort;
        const currentOrder = table.dataset.currentOrder;

        if (saved.sort === currentSort && saved.order === currentOrder) return;

        const url = new URL(window.location.href);
        url.searchParams.set('sort', saved.sort);
        url.searchParams.set('order', saved.order);

        if (window.htmx) {
            window.htmx.ajax('GET', url.toString(), { target: '#tabela-container', pushUrl: true });
        } else {
            window.location.href = url.toString();
        }
    }

    attachSortHandlers();
    captureSortFromTable();
    ensureSortPreferenceApplied();

    document.body.addEventListener('htmx:afterSwap', evt => {
        if (evt.detail.target.id === 'tabela-container') {
            loadColumnPreferences();
            applyColumnVisibility();
            attachSortHandlers();
            captureSortFromTable();
        }
    });
};

// --- LÓGICA DE SELEÇÃO E GRÁFICOS (DUPLOS) ---
let chartInstanceReo = null;
let chartInstanceVisc = null;

// Paleta de cores consistente para garantir que o mesmo lote tenha a mesma cor em ambos os gráficos (se necessário)
const CORES_GRAFICO = [
    '#0d6efd', '#dc3545', '#198754', '#ffc107', '#6f42c1', 
    '#fd7e14', '#20c997', '#0dcaf0', '#343a40', '#6610f2'
];

// 1. Monitora cliques nos checkboxes
document.addEventListener('change', function(e) {
    if (e.target.classList.contains('curve-selector') || e.target.id === 'selectAll') {
        atualizarBarraComparacao();
    }
});

function toggleAllCheckboxes(source) {
    document.querySelectorAll('.curve-selector').forEach(cb => cb.checked = source.checked);
    atualizarBarraComparacao();
}

function atualizarBarraComparacao() {
    const selecionados = document.querySelectorAll('.curve-selector:checked');
    const bar = document.getElementById('compareBar');
    const count = document.getElementById('countSelected');
    
    if (selecionados.length > 0) {
        bar.classList.remove('d-none');
        count.textContent = selecionados.length;
    } else {
        bar.classList.add('d-none');
    }
}

// 2. Função Principal: Buscar dados e Desenhar
async function abrirGraficoComparativo() {
    const selecionados = Array.from(document.querySelectorAll('.curve-selector:checked')).map(cb => cb.value);
    
    if (selecionados.length > 10) {
        alert("Por favor, selecione no máximo 10 curvas para não travar a visualização.");
        return;
    }

    // Abre o Modal
    const modal = new bootstrap.Modal(document.getElementById('chartModal'));
    modal.show();

    // Chama a API
    try {
        const response = await fetch(`/api/grafico?ids=${selecionados.join(',')}`);
        const data = await response.json();

        if (!response.ok) {
            // Lança erro com a mensagem vinda da API ou uma genérica
            throw new Error(data.error || "Erro desconhecido ao comunicar com o servidor");
        }

        if (data.error) {
            alert("Aviso do Servidor: " + data.error);
            return;
        }

        // Atualiza Título com os Materiais (Agrupamento)
        const materialsText = data.materiais ? data.materiais.join(' | ') : 'Vários Materiais';
        document.getElementById('modalMateriaisTitle').textContent = "Comparando: " + materialsText;

        // Renderiza (ou esconde) Gráfico de Reometria
        const temReo = data.reometria && data.reometria.length > 0;
        document.getElementById('containerReo').style.display = temReo ? 'block' : 'none';
        if (temReo) {
            chartInstanceReo = criarGraficoBase('chartReo', chartInstanceReo, data.reometria, 'Torque (lb.in)');
        }

        // Renderiza (ou esconde) Gráfico de Viscosidade
        const temVisc = data.viscosidade && data.viscosidade.length > 0;
        document.getElementById('containerVisc').style.display = temVisc ? 'block' : 'none';
        if (temVisc) {
            chartInstanceVisc = criarGraficoBase('chartVisc', chartInstanceVisc, data.viscosidade, 'Mooney (MU)');
        }

        // Aviso se tudo vazio
        document.getElementById('msgVazio').classList.toggle('d-none', temReo || temVisc);

    } catch (err) {
        console.error(err);
        alert("Erro ao carregar dados do gráfico: " + err.message);
    }
}

// 3. Função Genérica para Criar Gráficos com Chart.js
function criarGraficoBase(canvasId, chartInstance, datasets, yLabel) {
    const ctx = document.getElementById(canvasId).getContext('2d');

    // Destroi gráfico anterior se existir
    if (chartInstance) {
        chartInstance.destroy();
    }

    // Atribui cores sequenciais aos datasets
    datasets.forEach((ds, index) => {
        const cor = CORES_GRAFICO[index % CORES_GRAFICO.length];
        ds.borderColor = cor;
        ds.backgroundColor = cor;
        // Ajustes visuais para performance
        ds.pointRadius = 0; 
        ds.borderWidth = 2;
        ds.fill = false;
        ds.tension = 0.4;
    });

    return new Chart(ctx, {
        type: 'line',
        data: { datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        usePointStyle: true,
                        boxWidth: 8,
                        font: { size: 11 }
                    }
                },
                tooltip: {
                    callbacks: {
                        // Tooltip personalizada
                        title: function(context) {
                            return `Tempo: ${context[0].parsed.x}s`;
                        },
                        label: function(context) {
                            // Pega o nome do material que guardamos no dataset (se disponível) ou usa o label
                            const material = context.dataset.material || '';
                            const materialStr = material ? ` (${material})` : '';
                            return `${context.dataset.label}: ${context.parsed.y.toFixed(2)}${materialStr}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'linear',
                    title: { display: true, text: 'Tempo (s)' },
                    ticks: { maxTicksLimit: 10 }
                },
                y: {
                    title: { display: true, text: yLabel }
                }
            }
        }
    });
}

// 4. Função para Marcar/Desmarcar todas as curvas no gráfico (NOVO)
function toggleGraficoSelection(visible) {
    const updateChartVisibility = (chartInstance) => {
        if (!chartInstance) return;
        const setHidden = !visible;
        chartInstance.data.datasets.forEach(ds => {
            ds.hidden = setHidden;
        });
        chartInstance.update();
    };

    if (typeof chartInstanceReo !== 'undefined' && chartInstanceReo) {
        updateChartVisibility(chartInstanceReo);
    }
    if (typeof chartInstanceVisc !== 'undefined' && chartInstanceVisc) {
        updateChartVisibility(chartInstanceVisc);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
} else {
    initUI();
}