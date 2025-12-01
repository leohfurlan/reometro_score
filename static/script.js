document.addEventListener('DOMContentLoaded', function () {
    
    // --- 1. Lógica da Sidebar (Mantém o menu funcionando) ---
    const sidebarToggle = document.body.querySelector('#sidebarToggle');
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', event => {
            event.preventDefault();
            document.body.classList.toggle('sb-sidenav-toggled');
        });
    }

    // --- 2. Lógica do Modal de Detalhes ---
    const detailsModal = document.getElementById('detailsModal');
    
    if (detailsModal) {
        detailsModal.addEventListener('show.bs.modal', event => {
            // O botão que foi clicado
            const button = event.relatedTarget;
            
            // --- A. DADOS GERAIS (CABEÇALHO) ---
            const batch = button.getAttribute('data-batch');
            const material = button.getAttribute('data-material');
            const score = button.getAttribute('data-score');
            
            // Verifica se os elementos existem antes de tentar preencher
            // Tentamos encontrar pelos IDs mais prováveis baseados na sua imagem
            const tituloBatch = detailsModal.querySelector('#modalBatchId') || detailsModal.querySelector('#modalBatchDisplay');
            const textoMaterial = detailsModal.querySelector('#modalMaterial') || detailsModal.querySelector('#modalMaterialDisplay');
            const displayScore = detailsModal.querySelector('#modalScoreDisplay');

            if (tituloBatch) tituloBatch.textContent = (batch && batch !== 'None') ? 'BATCH: ' + batch : 'BATCH: N/A';
            if (textoMaterial) textoMaterial.textContent = 'Material: ' + material;
            
            if (displayScore) {
                displayScore.textContent = score;
                // Pinta de verde se >= 85, vermelho se menor
                displayScore.className = parseFloat(score) >= 85 ? 'text-success fw-bold fs-5' : 'text-danger fw-bold fs-5';
            }

            // --- B. FUNÇÃO PARA PREENCHER AS LINHAS (Ts2, T90, ML) ---
            function updateRow(paramName, targetId, measuredId) {
                // Pega os dados do botão (ex: data-ts2-target)
                const targetVal = button.getAttribute(`data-${paramName}-target`);
                const measuredVal = button.getAttribute(`data-${paramName}-measured`);
                const status = button.getAttribute(`data-${paramName}-status`); // success, danger ou dark

                // Encontra os lugares no HTML para escrever
                const targetElem = document.getElementById(targetId);
                const measuredElem = document.getElementById(measuredId);

                if (targetElem) targetElem.textContent = targetVal;
                
                if (measuredElem) {
                    measuredElem.textContent = measuredVal;
                    
                    // Limpa classes antigas e aplica as novas cores/ícones
                    measuredElem.className = 'fw-bold'; 
                    if (status === 'success') {
                        measuredElem.classList.add('text-success');
                        measuredElem.innerHTML += ' <i class="fas fa-check small ms-1"></i>';
                    } else if (status === 'danger') {
                        measuredElem.classList.add('text-danger');
                        measuredElem.innerHTML += ' <i class="fas fa-arrow-up small ms-1"></i>';
                    } else {
                        measuredElem.classList.add('text-dark');
                    }
                }
            }

            // --- C. EXECUTA PARA CADA PARÂMETRO ---
            // IMPORTANTE: Estes IDs (ts2Target, etc) devem existir no seu HTML do modal
            updateRow('ts2', 'ts2Target', 'ts2Measured');
            updateRow('t90', 't90Target', 't90Measured');
            updateRow('ml',  'mlTarget',  'mlMeasured');
        });
    }
});