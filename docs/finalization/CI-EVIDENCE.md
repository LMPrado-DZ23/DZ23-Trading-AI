# Evidência de CI para o estado atual

Um documento de estado só pode declarar uma CI atual como verde quando citar
um registro de `CI-EVIDENCE.json`. Cada registro contém o ID público do run e
o SHA completo que ele avaliou. O job **CI evidence integrity** consulta a API
do GitHub Actions e falha se a conclusão não for `success` ou se o SHA não
corresponder.

Registros históricos podem permanecer nos checkpoints como histórico, mas não
substituem a evidência do estado presente. Para registrar uma nova conclusão:

1. espere o run terminar com sucesso;
2. registre seu `run_id` e `head_sha` no JSON;
3. execute `python scripts/verify_ci_evidence.py --repository OWNER/REPO`;
4. só então atualize `STATUS.md`, `RELEASE_GATES.md` e os checkpoints.

No momento, não há alegação nova: o run do PR #15 ainda precisa terminar antes
de ser usado como evidência documental.
