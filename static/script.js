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
                const med = tempoMax && tempoMax.trim() ? `${tempoMax}s` : '--';
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

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
} else {
    initUI();
}
