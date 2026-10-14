# Matriz de rotas FM2 — rascunho mecânico + curadoria (§2 da missão)
Auditoria das rotas HTTP declaradas em `service/app.py` (branch `fix/instalador-atualizacoes-windows`). Coluna UI: primeira referência ao caminho em `service/static/` (vendor excluído); TESTE: até 3 ocorrências literais do caminho em `tests/`.
Total: **145 rotas** (0 duplicadas por MÉTODO+CAMINHO) · com UI: **93** · sem UI: **52** · com teste direto: **129** · sem teste direto: **16** (contagens pós-FM-3; ver Curadoria no fim).
Marcas: `(dinâmico)` = URL montada por concatenação no painel; SERVIÇO EXTERNO “se/condicional” = alcance só com configuração explícita do dono (feed real→exchange; DESK_BROKER=ccxt→corretora; provedor LLM→IA; canais→telegram/webhook/SMTP); no padrão da mesa/copiloto nada sai à rede (fixture/paper).
Rascunho mecânico gerado por auditoria; colunas RESULTADO/INSTALADO da matriz final são preenchidas na curadoria com evidência de execução.

### Auth, Setup e Administração de tenancy

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| POST `/v1/auth/bootstrap` | `tenant_bootstrap` (L426) | sem UI | tests/test_frontend_contract.py:47<br>tests/test_single_owner_g2.py:76 | nenhum |  |
| POST `/v1/auth/login` | `tenant_login` (L444) | sem UI | tests/e2e/test_panel_e2e.py:140<br>tests/test_frontend_contract.py:54<br>tests/test_signup.py:58 | nenhum |  |
| GET `/v1/auth/status` | `auth_status` (L456) | service/static/index.html:314 | tests/test_frontend_contract.py:20<br>tests/test_login_dono_unico.py:38<br>tests/test_single_owner_g2.py:158 | nenhum |  |
| POST `/v1/auth/setup` | `owner_setup` (L470) | service/static/index.html:252 | tests/test_login_dono_unico.py:51 | nenhum |  |
| POST `/v1/auth/recover` | `owner_recover` (L486) | service/static/index.html:255 | tests/test_login_dono_unico.py:116 | nenhum |  |
| POST `/v1/auth/recovery-code` | `owner_new_recovery_code` (L514) | service/static/index.html:256 | tests/test_login_dono_unico.py:153 | nenhum |  |
| GET `/v1/auth/me` | `auth_me` (L524) | service/static/index.html:254 | tests/e2e/test_panel_e2e.py:144<br>tests/test_frontend_contract.py:21<br>tests/test_login_dono_unico.py:45 | nenhum |  |
| POST `/v1/auth/logout` | `tenant_logout` (L531) | service/static/index.html:317 | tests/test_frontend_contract.py:22<br>tests/test_single_owner_g2.py:191 | nenhum |  |
| POST `/v1/admin/tenants` | `create_tenant` (L538) | sem UI | tests/test_single_owner_g2.py:80 | nenhum |  |
| POST `/v1/admin/users` | `create_tenant_user` (L550) | sem UI | tests/test_frontend_contract.py:48<br>tests/test_single_owner_g2.py:81 | nenhum |  |
| GET `/v1/admin/users` | `list_tenant_users` (L568) | sem UI | tests/test_frontend_contract.py:48<br>tests/test_single_owner_g2.py:81 | nenhum |  |
| GET `/v1/auth/signup-status` | `signup_status` (L1938) | sem UI | tests/test_signup.py:43<br>tests/test_single_owner_g2.py:73<br>tests/test_single_owner_modo_windows.py:28 | nenhum |  |
| POST `/v1/auth/signup` | `tenant_signup` (L1947) | sem UI | tests/e2e/test_panel_e2e.py:141<br>tests/test_frontend_contract.py:47<br>tests/test_signup.py:43 | nenhum |  |
| GET `/v1/auth/oauth/providers` | `oauth_providers` (L2079) | sem UI | tests/e2e/test_panel_e2e.py:142<br>tests/test_oauth_social.py:112<br>tests/test_single_owner_g2.py:77 | nenhum |  |
| GET `/v1/auth/oauth/{provider}/start` | `oauth_start` (L2086) | sem UI | tests/test_oauth_social.py:116<br>tests/test_single_owner_g2.py:78<br>tests/test_single_owner_modo_windows.py:34 | nenhum |  |
| GET `/v1/auth/oauth/{provider}/callback` | `oauth_callback` (L2097) | sem UI | tests/test_oauth_social.py:122<br>tests/test_single_owner_g2.py:79 | webhook externo |  |

### Serviço local, páginas e estáticos

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| POST `/v1/app/quit` | `app_quit` (L418) | service/static/index.html:318 | tests/test_frontend_contract.py:118<br>tests/test_service_leigo.py:689<br>tests/windows/test_desktop_launcher.py:525 | nenhum |  |
| GET `/healthz` | `hz` (L963) | sem UI | tests/e2e/conftest.py:85<br>tests/test_service_leigo.py:147<br>tests/test_signup.py:112 | nenhum |  |
| GET `/readyz` | `readyz` (L975) | service/static/index.html:319 | sem teste direto | nenhum |  |
| GET `/metrics` | `metrics` (L981) | sem UI | sem teste direto | nenhum |  |
| GET `/` | `index` (L985) | sem UI | tests/test_service_leigo.py:298<br>tests/test_signup.py:111<br>tests/test_site_publico.py:24 | nenhum |  |
| GET `/app` | `aplicacao` (L993) | service/static/site.html:78 | tests/e2e/test_aceite_td5.py:42<br>tests/e2e/test_panel_e2e.py:32<br>tests/test_ia_painel_static.py:172 | nenhum |  |
| GET `/app/` | `aplicacao` (L993) | sem UI | sem teste direto | nenhum |  |
| GET `/static/marca/{arquivo}` | `marca` (L999) | service/static/index.html:6 (via favicon.ico) | tests/test_service_leigo.py:656 | nenhum |  |
| GET `/favicon.ico` | `favicon` (L1018) | service/static/index.html:6 | tests/test_service_leigo.py:649 | nenhum |  |
| GET `/static/vendor/lightweight-charts/{arquivo}` | `vendor_lightweight_charts` (L1023) | service/static/index.html:185 (via arquivo do vendor) | tests/test_td3_graficos.py:70 | nenhum |  |
| GET `/manifest.webmanifest` | `manifest` (L1044) | service/static/index.html:10 | tests/e2e/test_aceite_td5.py:416<br>tests/test_identidade_visual.py:95<br>tests/test_pwa_static.py:24 | nenhum |  |
| GET `/sw.js` | `service_worker` (L1051) | service/static/index.html:658 | tests/e2e/test_aceite_td5.py:413<br>tests/e2e/test_panel_e2e.py:295<br>tests/test_pwa_static.py:54 | nenhum |  |
| GET `/floor` | `floor` (L1401) | service/static/index.html:181 | tests/e2e/test_panel_e2e.py:66<br>tests/test_frontend_contract.py:82<br>tests/test_pwa_push.py:385 | nenhum |  |
| GET `/v1/status/operacao` | `status_operacao` (L2017) | service/static/index.html:211 | tests/test_frontend_contract.py:32<br>tests/test_identidade_visual.py:79<br>tests/test_modo_simples.py:187 | nenhum | Docstring documenta lacuna: /v1/operational-readiness (por inquilino) "ainda não implementado". |

### Configuração e Setup Inteligente

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/config` | `get_cfg` (L1087) | service/static/index.html:198 | tests/test_alertas_transparencia.py:122<br>tests/test_auditoria_blockers.py:328<br>tests/test_auditoria_fin1_live_desk_seal.py:96 | nenhum |  |
| POST `/v1/config` | `set_cfg` (L1107) | service/static/index.html:198 | tests/test_alertas_transparencia.py:122<br>tests/test_auditoria_blockers.py:328<br>tests/test_auditoria_fin1_live_desk_seal.py:96 | nenhum |  |
| GET `/v1/setup/estado` | `setup_estado` (L1226) | service/static/index.html:204 | tests/test_ia_painel_static.py:248<br>tests/test_modo_simples.py:228<br>tests/test_smart_setup.py:418 | nenhum |  |
| POST `/v1/setup/aplicar` | `setup_aplicar` (L1240) | service/static/index.html:355 | tests/test_ia_painel_static.py:249<br>tests/test_smart_setup.py:436<br>tests/test_smart_setup_homologacao.py:81 | nenhum |  |
| GET `/v1/setup/diagnostico` | `setup_diagnostico` (L1265) | sem UI | tests/test_smart_setup.py:423<br>tests/test_smart_setup_homologacao.py:193 | nenhum |  |
| GET `/v1/primeiros-passos` | `onboarding` (L3772) | service/static/index.html:379 | tests/test_service_leigo.py:191 | nenhum |  |

### IA (provedores, modelos, consumo) e camada quant

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/ai/provedores` | `ai_listar_provedores` (L1187) | service/static/index.html:326 | tests/test_ia_painel_static.py:97<br>tests/test_ia_provedores.py:440 | nenhum |  |
| POST `/v1/ai/testar` | `ai_testar_provedor` (L1192) | service/static/index.html:332 | tests/test_ia_painel_static.py:97<br>tests/test_ia_provedores.py:441<br>tests/test_smart_setup_homologacao.py:190 | provedor IA |  |
| GET `/v1/ai/modelos` | `ai_listar_modelos` (L1205) | service/static/index.html:336 | tests/test_ia_painel_static.py:98<br>tests/test_ia_provedores.py:442 | provedor IA |  |
| GET `/v1/ai/uso` | `ai_uso` (L1215) | service/static/index.html:356 | tests/test_ia_painel_static.py:98<br>tests/test_ia_provedores.py:674 | nenhum |  |
| GET `/v1/quant/status` | `quant_status` (L4064) | service/static/index.html:617 | tests/e2e/test_panel_e2e.py:152<br>tests/test_quant_intelligence.py:373 | nenhum |  |

### Análises e Jobs

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| POST `/v1/jobs` | `new_job` (L1276) | service/static/index.html:374 | tests/test_frontend_contract.py:24<br>tests/test_service_leigo.py:44<br>tests/test_single_owner_g2.py:191 | provedor IA |  |
| GET `/v1/jobs` | `list_jobs` (L1296) | service/static/index.html:375 | tests/test_frontend_contract.py:24<br>tests/test_service_leigo.py:44<br>tests/test_single_owner_g2.py:191 | nenhum |  |
| GET `/v1/jobs/health` | `jobs_health` (L1302) | service/static/index.html:319 | tests/test_frontend_contract.py:25 | nenhum |  |
| GET `/v1/jobs/{jid}` | `get_job` (L1307) | service/static/index.html:376 (dinâmico) | tests/test_service_leigo.py:44 | nenhum |  |
| POST `/v1/jobs/{jid}/cancel` | `cancel` (L1315) | service/static/index.html:376 (dinâmico) | sem teste direto | nenhum |  |

### Corretoras, Execução e Risco

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/personal/exchanges` | `personal_exchanges` (L1324) | service/static/index.html:400 | tests/test_personal_exchanges_endpoint.py:10<br>tests/test_fm3_corretoras.py:101 | nenhum |  |
| GET `/v1/personal/exchanges/{exchange_id}/capabilities` | `personal_exchange_capabilities` (L1356) | service/static/index.html:411 (dinâmico) | tests/test_fm3_corretoras.py:27 | nenhum |  |
| GET `/v1/broker/status` | `broker_status` (L1377) | service/static/index.html:373 | tests/test_service_leigo.py:58 | nenhum |  |
| POST `/v1/broker/order` | `broker_order` (L1395) | sem UI | sem teste direto | nenhum | Stub legado: responde 410 "endpoint legado permanentemente removido" — usar /v1/execution/paper ou o ExecutionRouter. |
| POST `/v1/execution/paper` | `paper_execution` (L1665) | sem UI | sem teste direto | nenhum |  |
| GET `/v1/execution/shadow` | `shadow_summary` (L1688) | service/static/index.html:377 | sem teste direto | nenhum |  |
| POST `/v1/execution/shadow` | `shadow_execution` (L1693) | service/static/index.html:377 | sem teste direto | nenhum |  |
| GET `/v1/execution/health` | `execution_health` (L1707) | service/static/index.html:377 | tests/test_frontend_contract.py:27 | nenhum |  |
| POST `/v1/execution/reconciliation` | `execution_reconciliation` (L1713) | sem UI | sem teste direto | nenhum |  |
| POST `/v1/execution/kill-switch` | `execution_kill_switch` (L1719) | service/static/index.html:378 | tests/test_kill_switch_runtime.py:75 | nenhum |  |

### Agência / Mesa de Agentes (agency/office)

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/agency/snapshot` | `agency_snapshot` (L1407) | service/static/floor.html:21 | tests/test_trading_floor.py:14 | nenhum |  |
| GET `/v1/agency/events` | `agency_events` (L1417) | service/static/index.html:319 | tests/test_trading_floor.py:14 | nenhum |  |
| GET `/v1/agency/health` | `agency_health` (L1424) | service/static/index.html:319 | tests/test_frontend_contract.py:26<br>tests/test_service_leigo.py:249 | nenhum |  |
| GET `/v1/agency/tasks` | `agency_tasks` (L1452) | sem UI | tests/test_service_leigo.py:222 | nenhum |  |
| GET `/v1/agency/memory` | `agency_memory` (L1459) | sem UI | sem teste direto | nenhum |  |
| GET `/v1/agency/missions` | `agency_missions` (L1466) | service/static/floor.html:22 | tests/test_frontend_contract.py:93 | nenhum |  |
| POST `/v1/agency/missions` | `agency_create_mission` (L1471) | service/static/floor.html:22 | tests/test_frontend_contract.py:93 | nenhum |  |
| POST `/v1/agency/tasks` | `agency_submit_task` (L1486) | sem UI | tests/test_service_leigo.py:222 | nenhum |  |
| POST `/v1/agency/tasks/{task_id}/approve` | `agency_approve_task` (L1511) | service/static/floor.html:23 (dinâmico) | tests/test_service_leigo.py:229 | nenhum |  |
| POST `/v1/agency/tasks/run` | `agency_run_tasks` (L1522) | sem UI | tests/test_service_leigo.py:228 | provedor IA |  |
| GET `/v1/agency/worker/status` | `agency_worker_status` (L1539) | sem UI | tests/test_service_leigo.py:241 | nenhum |  |
| POST `/v1/agency/worker/start` | `agency_worker_start` (L1547) | sem UI | tests/test_service_leigo.py:239 | provedor IA |  |
| POST `/v1/agency/worker/stop` | `agency_worker_stop` (L1568) | sem UI | tests/test_service_leigo.py:243 | nenhum |  |
| GET `/v1/office/snapshot` | `office_snapshot` (L1576) | sem UI | tests/test_kill_switch_runtime.py:57 | nenhum |  |
| GET `/v1/office/events` | `office_events` (L1598) | sem UI | tests/test_kill_switch_runtime.py:65 | nenhum |  |
| POST `/v1/agency/agents/{agent_id}/status` | `agency_agent_status` (L1615) | sem UI | sem teste direto | nenhum |  |
| POST `/v1/agency/meetings` | `agency_meeting` (L1628) | sem UI | sem teste direto | nenhum |  |
| POST `/v1/agency/meetings/{meeting_id}/votes` | `agency_vote` (L1638) | sem UI | sem teste direto | nenhum |  |
| GET `/v1/agency/stream` | `agency_stream` (L1651) | sem UI | sem teste direto | nenhum |  |

### Mesa de Agentes (desk) e gates de dinheiro real (LIVE)

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/desk/status` | `desk_status` (L2914) | service/static/index.html:201 | tests/test_alertas_transparencia.py:163<br>tests/test_auditoria_fin1_live_desk_seal.py:99<br>tests/test_auditoria_fin2_gates_financeiros.py:168 | nenhum |  |
| POST `/v1/desk/cycle` | `desk_cycle` (L2958) | service/static/index.html:391 | tests/test_livro_vivo_e_reinicio.py:51<br>tests/test_modo_da_mesa.py:199<br>tests/test_qoder_maestro_traders.py:205 | condicional: exchange (CCXT), provedor IA, canais de alerta |  |
| POST `/v1/desk/scheduler/{action}` | `desk_scheduler` (L2983) | service/static/index.html:244 (dinâmico) | tests/test_service_leigo.py:190 | condicional: exchange (CCXT), provedor IA, canais de alerta |  |
| GET `/v1/desk/modo` | `desk_modo_obter` (L3002) | service/static/index.html:581 | tests/test_decisao_mesa.py:181<br>tests/test_modo_da_mesa.py:197 | nenhum |  |
| POST `/v1/desk/modo` | `desk_modo_definir` (L3009) | service/static/index.html:581 | tests/test_decisao_mesa.py:181<br>tests/test_modo_da_mesa.py:197 | nenhum |  |
| POST `/v1/desk/traders/{agent_id}/promover` | `desk_trader_promover` (L3032) | service/static/index.html:394 (dinâmico) | tests/test_qoder_maestro_traders.py:173 | nenhum |  |
| POST `/v1/desk/traders/{agent_id}/pausar` | `desk_trader_pausar` (L3056) | service/static/index.html:394 (dinâmico) | tests/test_qoder_maestro_traders.py:202 | nenhum |  |
| POST `/v1/desk/traders/{agent_id}/retomar` | `desk_trader_retomar` (L3076) | service/static/index.html:394 (dinâmico) | tests/test_qoder_maestro_traders.py:210 | nenhum |  |
| GET `/v1/desk/feed` | `desk_feed` (L3096) | service/static/index.html:584 | tests/test_frontend_contract.py:97<br>tests/test_service_leigo.py:189 | exchange (CCXT) se feed real |  |
| GET `/v1/desk/avaliacao` | `desk_evaluation` (L3122) | service/static/index.html:587 | tests/test_service_leigo.py:190 | nenhum |  |
| GET `/v1/desk/traders/{agent_id}/sala` | `desk_trader_sala` (L3242) | service/static/index.html:423 (dinâmico) | tests/test_sala_trader.py:429<br>tests/test_td3_graficos.py:216 | exchange (CCXT) se feed real |  |
| POST `/v1/desk/traders/{agent_id}/sala/leitura` | `desk_trader_sala_leitura` (L3250) | service/static/index.html:576 (dinâmico) | tests/test_td3_graficos.py:216 | provedor IA se configurada; exchange (CCXT) se feed real |  |
| GET `/v1/desk/aprendizado` | `desk_learning` (L4007) | service/static/index.html:597 | tests/test_alertas_transparencia.py:205<br>tests/test_desk_inteligencia.py:306 | nenhum |  |
| GET `/v1/desk/viabilidade` | `desk_viability` (L4024) | service/static/index.html:602 | tests/test_desk_custos.py:155 | nenhum |  |
| POST `/v1/desk/reconciliar` | `desk_reconciliar` (L4842) | service/static/index.html:417 | tests/test_auditoria_fin3_resposta_perdida.py:218<br>tests/test_fm3_corretoras.py:120 | exchange (CCXT) se DESK_BROKER=ccxt |  |
| GET `/v1/live/status` | `live_status` (L3906) | service/static/index.html:590 | tests/test_auditoria_fin1_live_desk_seal.py:109<br>tests/test_auditoria_fin2_gates_financeiros.py:169<br>tests/test_service_leigo.py:515 | nenhum |  |
| POST `/v1/live/parada-emergencia` | `live_emergency` (L3948) | service/static/index.html:595 | tests/test_auditoria_fin2_gates_financeiros.py:170<br>tests/test_service_leigo.py:538 | nenhum |  |
| POST `/v1/live/liberar` | `live_resume` (L3976) | sem UI | tests/test_service_leigo.py:542 | nenhum |  |

### Escola, Salas, Maestro e Pilotos

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/escola/central` | `escola_central` (L3339) | service/static/index.html:606 | tests/test_service_escola.py:45 | nenhum |  |
| GET `/v1/escola/salas` | `escola_salas` (L3345) | service/static/index.html:606 | tests/test_painel_global.py:271<br>tests/test_service_escola.py:38 | nenhum |  |
| POST `/v1/escola/salas` | `escola_abrir_sala` (L3355) | service/static/index.html:606 | tests/test_painel_global.py:271<br>tests/test_service_escola.py:38 | nenhum |  |
| GET `/v1/escola/salas/detalhe` | `escola_sala_detalhe` (L3371) | sem UI | tests/test_service_escola.py:45 | nenhum |  |
| GET `/v1/escola/salas/pacote` | `escola_pacote` (L3380) | sem UI | tests/test_service_escola.py:46 | nenhum |  |
| POST `/v1/escola/salas/conhecimento` | `escola_adotar_conhecimento` (L3391) | sem UI | tests/test_service_escola.py:46 | nenhum |  |
| GET `/v1/escola/certificacao` | `escola_certificacao` (L3439) | sem UI | tests/test_service_escola.py:47 | nenhum |  |
| POST `/v1/escola/avaliar` | `escola_avaliar` (L3462) | sem UI | tests/test_service_escola.py:266 | nenhum |  |
| GET `/v1/escola/maestro` | `escola_maestro` (L3494) | sem UI | tests/test_service_escola.py:47 | nenhum |  |
| POST `/v1/escola/maestro/{acao}` | `escola_maestro_acao` (L3500) | sem UI | tests/test_service_escola.py:48 | nenhum |  |
| GET `/v1/maestro/painel` | `maestro_painel` (L3544) | service/static/index.html:395 | tests/test_painel_global.py:265 | nenhum |  |
| GET `/v1/escola/pilotos` | `escola_pilotos` (L3634) | service/static/index.html:606 | tests/test_service_escola.py:48 | nenhum |  |
| POST `/v1/escola/pilotos/solicitar` | `escola_piloto_solicitar` (L3647) | sem UI | tests/test_service_escola.py:49 | nenhum |  |
| POST `/v1/escola/pilotos/{agent_id}/autorizar` | `escola_piloto_autorizar` (L3718) | sem UI | tests/test_service_escola.py:50 | nenhum |  |
| POST `/v1/escola/pilotos/{agent_id}/revogar` | `escola_piloto_revogar` (L3736) | sem UI | tests/test_service_escola.py:51 | nenhum |  |

### Copiloto Manual (E3)

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/manual/modo` | `manual_modo_obter` (L4421) | service/static/index.html:644 | tests/test_copiloto_manual_api.py:54<br>tests/test_copiloto_manual_ui.py:29 | nenhum |  |
| PUT `/v1/manual/modo` | `manual_modo_definir` (L4427) | service/static/index.html:644 | tests/test_copiloto_manual_api.py:54<br>tests/test_copiloto_manual_ui.py:29 | nenhum |  |
| POST `/v1/manual/positions` | `manual_criar_posicao` (L4442) | service/static/index.html:636 | tests/e2e/test_aceite_td5.py:539<br>tests/test_copiloto_manual_api.py:43<br>tests/test_copiloto_manual_ui.py:30 | nenhum |  |
| GET `/v1/manual/positions` | `manual_listar_posicoes` (L4470) | service/static/index.html:636 | tests/e2e/test_aceite_td5.py:539<br>tests/test_copiloto_manual_api.py:43<br>tests/test_copiloto_manual_ui.py:30 | nenhum |  |
| GET `/v1/manual/positions/{posicao_id}` | `manual_obter_posicao` (L4489) | service/static/index.html:646 (dinâmico) | tests/test_copiloto_manual_api.py:356 | nenhum |  |
| POST `/v1/manual/positions/{posicao_id}/events` | `manual_registrar_evento` (L4504) | service/static/index.html:648 (dinâmico) | tests/test_copiloto_manual_api.py:357 | nenhum |  |
| PUT `/v1/manual/positions/{posicao_id}/monitoring-policy` | `manual_definir_politica` (L4529) | service/static/index.html:647 (dinâmico) | tests/test_copiloto_manual_api.py:358 | nenhum |  |
| POST `/v1/manual/positions/{posicao_id}/pause` | `manual_pausar` (L4551) | service/static/index.html:649 (dinâmico) | tests/test_copiloto_manual_api.py:359 | nenhum |  |
| POST `/v1/manual/positions/{posicao_id}/resume` | `manual_retomar` (L4564) | service/static/index.html:649 (dinâmico) | tests/test_copiloto_manual_api.py:360 | nenhum |  |
| POST `/v1/manual/positions/{posicao_id}/revisao` | `manual_revisao` (L4576) | service/static/index.html:650 (dinâmico) | tests/test_copiloto_manual_api.py:186<br>tests/test_copiloto_manual_ui.py:34 | provedor IA se configurada |  |
| GET `/v1/manual/positions/{posicao_id}/alerts` | `manual_alertas_da_posicao` (L4594) | service/static/index.html:651 (dinâmico) | tests/test_copiloto_manual_api.py:361 | nenhum |  |
| POST `/v1/manual/alerts/{alerta_id}/ack` | `manual_ack` (L4617) | service/static/index.html:652 (dinâmico) | tests/test_copiloto_manual_api.py:362 | nenhum |  |
| GET `/v1/manual/monitor/health` | `manual_monitor_health` (L4630) | service/static/index.html:653 | tests/test_copiloto_manual_api.py:60<br>tests/test_copiloto_manual_ui.py:36 | nenhum |  |
| GET `/v1/manual/sinais` | `manual_sinais` (L4646) | service/static/index.html:639 | tests/test_copiloto_sinais.py:229 | exchange (CCXT) se feed real |  |

### Alertas, Notificações, Telegram e PWA/Push

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/alertas` | `listar_alertas` (L4114) | service/static/index.html:623 | tests/test_alertas_transparencia.py:112<br>tests/test_centro_de_alertas.py:462 | nenhum |  |
| POST `/v1/alertas/verificar` | `disparar_alertas` (L4146) | sem UI | sem teste direto | telegram/webhook externo |  |
| GET `/v1/notificacoes/canais` | `canais_de_notificacao` (L4165) | service/static/index.html:366 | tests/test_centro_de_alertas.py:397 | nenhum |  |
| POST `/v1/notificacoes/testar` | `testar_canal_de_notificacao` (L4186) | service/static/index.html:370 | tests/test_centro_de_alertas.py:407 | telegram/webhook externo/SMTP |  |
| GET `/v1/notificacoes/inbox` | `inbox_de_notificacoes` (L4241) | service/static/index.html:366 | tests/test_centro_de_alertas.py:432 | nenhum |  |
| POST `/v1/notificacoes/{alerta_id}/ack` | `ack_de_notificacao` (L4256) | service/static/index.html:371 (dinâmico) | tests/test_centro_de_alertas.py:434 | nenhum |  |
| GET `/v1/notificacoes/matriz` | `ler_matriz_de_notificacao` (L4267) | service/static/index.html:366 | tests/test_centro_de_alertas.py:469 | nenhum |  |
| PUT `/v1/notificacoes/matriz` | `salvar_matriz_de_notificacao` (L4274) | service/static/index.html:366 | tests/test_centro_de_alertas.py:469 | nenhum |  |
| GET `/v1/push/estado` | `push_estado` (L4291) | service/static/index.html:672 | tests/test_pwa_push.py:363<br>tests/test_pwa_static.py:106 | nenhum |  |
| POST `/v1/push/vapid/gerar` | `push_vapid_gerar` (L4304) | service/static/index.html:703 | tests/test_pwa_push.py:398 | nenhum |  |
| POST `/v1/push/assinar` | `push_assinar` (L4330) | service/static/index.html:696 | tests/test_pwa_push.py:419<br>tests/test_pwa_static.py:57 | nenhum |  |
| POST `/v1/push/cancelar` | `push_cancelar` (L4369) | service/static/index.html:717 | tests/test_pwa_push.py:461<br>tests/test_pwa_static.py:57 | nenhum |  |
| POST `/v1/telegram/webhook` | `webhook_telegram` (L4387) | sem UI | tests/test_centro_de_alertas.py:440 | telegram |  |

### Faturamento e Webhooks de terceiros

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/billing/planos` | `billing_plans` (L1841) | sem UI | tests/test_billing_woovi.py:185<br>tests/test_single_owner_g2.py:82 | nenhum |  |
| GET `/v1/billing/status` | `billing_status` (L1849) | sem UI | tests/test_billing_woovi.py:184<br>tests/test_single_owner_g2.py:83 | nenhum |  |
| POST `/v1/billing/checkout` | `billing_checkout` (L1857) | sem UI | tests/test_billing_woovi.py:186<br>tests/test_single_owner_g2.py:84 | webhook externo (Woovi) |  |
| POST `/v1/webhooks/woovi` | `woovi_webhook` (L1874) | sem UI | tests/test_billing_woovi.py:193<br>tests/test_single_owner_g2.py:85<br>tests/test_single_owner_modo_windows.py:29 | webhook externo |  |

### Admin, Infra, Backup e Portabilidade

| ROTA | BACKEND | UI | TESTE | SERVIÇO EXTERNO | PENDÊNCIA |
| --- | --- | --- | --- | --- | --- |
| GET `/v1/admin/database` | `database_health` (L3812) | sem UI | tests/test_single_owner_g2.py:166<br>tests/test_supabase.py:99 | webhook externo (Supabase REST) |  |
| POST `/v1/backup` | `backup_now` (L3749) | service/static/index.html:589 | tests/test_service_leigo.py:191 | nenhum |  |
| GET `/v1/backups` | `list_backups` (L3762) | sem UI | sem teste direto | nenhum |  |
| GET `/v1/mudanca/exportar` | `exportar_tudo` (L4750) | service/static/index.html:627 | tests/test_alertas_transparencia.py:164 | nenhum |  |
| POST `/v1/mudanca/importar` | `importar_tudo` (L4782) | sem UI | tests/test_alertas_transparencia.py:198 | nenhum |  |
| GET `/publico/{tenant_id}` | `pagina_publica` (L4709) | sem UI | tests/test_alertas_transparencia.py:118 | nenhum |  |

---

## Curadoria FM-2/FM-3 (§2 da missão) — cobertura por rota com evidência de execução

**Método.** O rascunho foi gerado por extração das rotas declaradas em `service/app.py` (145 no total; 0 duplicadas por MÉTODO+CAMINHO) e varredura literal de referências em `service/static/` (UI — `vendor/` excluído) e `tests/` (teste direto). A curadoria fixa as contagens pós-FM-3, mapeia as colunas exigidas pelo §2 e registra os buracos restantes sem os ocultar.

**Mapeamento das colunas §2 (REQUISITO | BACKEND | UI | TESTE | INSTALADO | SERVIÇO EXTERNO | RESULTADO | PENDÊNCIA).** O rascunho abriga BACKEND, UI, TESTE, SERVIÇO EXTERNO e PENDÊNCIA linha a linha; REQUISITO, INSTALADO e RESULTADO são transversais (por grupo, abaixo):

- **REQUISITO (§2):** os 13 grupos das seções acima cobrem as seções da missão — Auth/Setup/Tenancy = superfície SaaS (`G2`, 404 por política); Corretoras/execução = §3/§4; Mesa/Escola/Pilotos = §5/§6; Manual/Copiloto = §7; IA = §8; Alertas/Notificações/PWA = §9; Admin/Infra/Backup = §10/§11.
- **INSTALADO (§11/§13):** TODAS as 145 rotas são servidas pelo MESMO `service/app.py` congelado no exe `dz23-windows-x64`; a prova de execução no artefato é o aceite TD-5 no bundle (13/13) + a matriz do instalador (`TOTAL: 0 falha(s)`), re-executados no CI do commit desta curadoria (runs citados no STATE/CHECKPOINTS pós-push; última citação pré-FM: Release `38616157472` / Matriz `38616157674`, commit `929ebd4`). **As 14 rotas da camada SaaS respondem 404 POR POLÍTICA no instalado** (gate single-owner `SAAS_BLOCKED_PREFIXES`, app.py:269 — não é falha de cobertura): `/v1/auth/bootstrap`, `/v1/auth/login`, `/v1/auth/signup`, `/v1/auth/signup-status`, `/v1/auth/oauth/providers`, `/v1/auth/oauth/{provider}/start`, `/v1/auth/oauth/{provider}/callback`, `/v1/admin/tenants`, `/v1/admin/users` (GET+POST), `/v1/billing/planos`, `/v1/billing/status`, `/v1/billing/checkout`, `/v1/webhooks/woovi`.
- **RESULTADO (execução fresca, 2026-10-14):** suíte completa na árvore do commit **`3034 passed / 6 skipped / 71 subtests`** (exit 0, 303,00 s) e **e2e Playwright 27/27** (49,39 s, exit 0) em instância isolada do harness — a instância do dono (`:8000`) nunca foi tocada. Os 6 skips são os declarados com razão de sempre (playwright fora do runner unit 2×, `langchain_aws` 1×, POSIX 600 em NTFS 1×, `DATABASE_URL` 1×, `lightgbm` 1×). **FM-4 (commit `f8742df`):** suíte completa **`3037 passed / 6 skipped / 71 subtests`** (exit 0, 334,93 s — mesmos 6 skips) + contrato read-only por descoberta das rotas manuais **3/3**; CI do push `f8742df` Release `38627361507` verde (aceite 13/13 em 30,97 s). FM-3: 15/15 em unidade (`tests/test_fm3_corretoras.py`) + 1 e2e novo (`test_fm3_corretora_catalogo_capacidades_e_preparo_real`, capturas `docs/mission/evidence/e2e/09c|09d|09e-*.png`); FM-1: 4/4 em unidade (`TestCapacidadesVisiveisNoModoSimples`) + item `td5-01` estendido no e2e de aceite (captura `td5-01-capacidades.png`).
- **PENDÊNCIA (§2 — declarada, nada oculto):** **16 rotas sem nenhuma ocorrência literal em `tests/`** (contagem pós-FM-3; a FM-3 fechou `GET /v1/personal/exchanges/{id}/capabilities`): `GET /readyz` (L975), `GET /metrics` (L981), `GET /app/` (L993), `POST /v1/jobs/{jid}/cancel` (L1315), `POST /v1/broker/order` (L1395 — stub legado 410), `POST /v1/execution/paper` (L1665), `GET /v1/execution/shadow` (L1688), `POST /v1/execution/shadow` (L1693), `POST /v1/execution/reconciliation` (L1713), `GET /v1/agency/memory` (L1459), `POST /v1/agency/agents/{agent_id}/status` (L1615), `POST /v1/agency/meetings` (L1628), `POST /v1/agency/meetings/{meeting_id}/votes` (L1638), `GET /v1/agency/stream` (L1651), `POST /v1/alertas/verificar` (L4146), `GET /v1/backups` (L3762). Nenhuma delas foi tocada por FM-1/FM-3; são buracos de cobertura DECLARADOS (infra/stub/agência) — candidatos à próxima rodada de auditoria.
