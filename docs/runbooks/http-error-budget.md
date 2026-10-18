# Runbook: DZ23HttpErrorBudgetBurn

Separe as séries 5xx por template de rota e correlacione os logs pelo `X-Request-ID`. Não pesquise por corpo, token ou conta. Verifique dependências da rota, saturação e mudanças recentes. Se a rota tocar decisão, risco, reconciliação ou execução, arme a parada de emergência e mantenha PAPER/SHADOW.

Recuperação exige taxa abaixo de 1% por duas janelas completas, reconciliação sem divergência e registro dos IDs de correlação relevantes.
