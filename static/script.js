const initUI = () => {
    // --- 1. L�"GICA DA SIDEBAR (TOGGLE VIA WRAPPER) ---
    const sidebarToggle = document.getElementById('sidebarToggle');
    const wrapper = document.getElementById('wrapper');

    if (sidebarToggle && wrapper) {
        sidebarToggle.addEventListener('click', event => {
            event.preventDefault();

            // Adiciona ou remove a classe 'toggled' no ID wrapper
            wrapper.classList.toggle('toggled');

            // Salva estado (opcional)
            const isClosed = wrapper.classList.contains('toggled');
            localStorage.setItem('sidebar-closed', isClosed);
        });

        // (Opcional) Restaurar estado ao carregar a pǭgina
        const savedState = localStorage.getItem('sidebar-closed');
        if (savedState === 'true') {
            wrapper.classList.add('toggled');
        }
    }

    // 2. Modal de Detalhes Completo
    const detailsModal = document.getElementById('detailsModal');

    if (detailsModal) {
        detailsModal.addEventListener('show.bs.modal', event => {
            const button = event.relatedTarget;

            // --- A. CABE��ALHO ---
            const batch = button.getAttribute('data-batch');
            const material = button.getAttribute('data-material');
            const score = button.getAttribute('data-score');
            const displayBatch = (batch && batch !== 'None') ? batch : 'N/A';

            // Preenche textos com fallback de seguran��a
            const setSafeText = (id, val) => {
                const el = detailsModal.querySelector(id);
                if (el) el.textContent = val;
            };

            setSafeText('#modalBatchDisplay', 'BATCH: ' + displayBatch);
            setSafeText('#modalMaterialDisplay', 'Material: ' + material);

            const scoreElem = detailsModal.querySelector('#modalScoreDisplay');
            if (scoreElem) {
                scoreElem.textContent = score;
                scoreElem.className = parseFloat(score) >= 85 ? 'text-success fw-bold fs-5' : 'text-danger fw-bold fs-5';
            }

            // --- B. FUN��ǟO DE PREENCHIMENTO (Agora com Min/Max) ---
            function updateParam(prefix, minId, targetId, maxId, measuredId) {
                const minVal = button.getAttribute(`data-${prefix}-min`);
                const targetVal = button.getAttribute(`data-${prefix}-target`);
                const maxVal = button.getAttribute(`data-${prefix}-max`);

                const measuredVal = button.getAttribute(`data-${prefix}-measured`);
                const status = button.getAttribute(`data-${prefix}-status`);

                // Preencher Especifica����es
                setSafeText(`#${minId}`, minVal);
                setSafeText(`#${targetId}`, targetVal);
                setSafeText(`#${maxId}`, maxVal);

                // Preencher Medido e Estilizar
                const measuredElem = detailsModal.querySelector(`#${measuredId}`);
                if (measuredElem) {
                    measuredElem.textContent = measuredVal;
                    measuredElem.className = 'fw-bold fs-5'; // Reset

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

            // --- C. EXECUTAR ---
            updateParam('ts2', 'ts2Min', 'ts2Target', 'ts2Max', 'ts2Measured');
            updateParam('t90', 't90Min', 't90Target', 't90Max', 't90Measured');
            updateParam('ml', 'mlMin', 'mlTarget', 'mlMax', 'mlMeasured');
        });
    }

    // --- 3. GESTǟO DE COLUNAS (Exibir/Ocultar) ---

    // Fun��ǜo para aplicar a visibilidade das colunas
    function applyColumnVisibility() {
        document.querySelectorAll('.col-toggle').forEach(checkbox => {
            const targetClass = checkbox.getAttribute('data-target');
            const isVisible = checkbox.checked;

            // Seleciona todas as cǸlulas (TH e TD) com essa classe
            document.querySelectorAll('.' + targetClass).forEach(el => {
                if (isVisible) {
                    el.style.display = ''; // Volta ao padrǜo (table-cell)
                } else {
                    el.style.display = 'none';
                }
            });
        });
    }

    // Escuta mudan��as nos checkboxes do dropdown
    document.querySelectorAll('.col-toggle').forEach(checkbox => {
        checkbox.addEventListener('change', applyColumnVisibility);
    });

    // Impede que o dropdown feche ao clicar no checkbox
    document.querySelectorAll('.checkbox-stop').forEach(el => {
        el.addEventListener('click', e => e.stopPropagation());
    });

    // --- HTMX: Reaplicar regras ap��s troca de pǭgina ---
    document.body.addEventListener('htmx:afterSwap', function (evt) {
        // Se o conteǧdo trocado foi a tabela, reaplica a visibilidade
        if (evt.detail.target.id === 'tabela-container') {
            applyColumnVisibility();
        }
    });
};

// Garante execu��ǟo mesmo se o script for carregado ap��s o DOM pronto
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
} else {
    initUI();
}
