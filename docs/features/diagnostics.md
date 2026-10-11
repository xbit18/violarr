# Diagnosticare una release mancante

**Diagnostica** nella WebUI mostra cosa succede all'interno di Violarr:
parametri, ricerca nello snapshot locale, filtri e ranking, e generazione della
risposta Torznab. Non cambia la ricerca e non verifica se un client a valle
accetta le release.

## Eseguire una ricerca

1. Apri **Diagnostica → Ricerca**, la vista predefinita.
2. Scegli ricerca generica, film o serie TV e inserisci il testo.
3. Apri **Parametri avanzati** per IMDb, TMDb (solo film), stagione/episodio
   (solo TV), categorie, limite e offset. Limite: 1–200; offset: 0–1000000. Le
   categorie sono accettate ma non filtrano le query attuali. La diagnostica
   conserva questo comportamento, senza ampliare le ricerche o disattivare i
   filtri.
4. Premi **Esegui diagnostica** e controlla gli stati delle fasi. Le fasi
   opzionali di ricerca/fusione compaiono solo se osservate; restano le quattro
   fasi esistenti.
5. Esamina candidati, motivo di esclusione, punteggio/indici delle regole e
   correzione dei sottotitoli. In **Parametri, impostazioni e finestre** puoi
   confrontare input originale e normalizzato, preset/regole effettive e
   finestre.

Gli indici delle regole partono da zero e corrispondono alle regole nelle
impostazioni del report. La lingua XML di una release restituita è distinta
dall'effetto della correzione dei sottotitoli durante l'elaborazione.

## Interpretare il risultato

| Osservazione                             | Cosa significa                                                                                                       |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Nessun candidato                         | Nessuna release recuperata dalle finestre esaminate dello snapshot locale, non prova di assenza dall'intero database |
| Esclusa dai filtri                       | Il preset o una regola ha escluso il candidato; controlla motivo e impostazioni effettive                            |
| Fuori dalla pagina finale                | Il candidato è stato mantenuto, ma non selezionato per questa pagina                                                 |
| Selezionata, restituzione non confermata | La selezione per XML non prova che serializzazione e validazione siano riuscite                                      |
| Restituita in XML validato               | Violarr ha generato la release nell'output; l'accettazione di Prowlarr/Sonarr/Radarr resta da verificare             |
| Fase non riuscita / non eseguita         | La pipeline è parziale; consulta l'errore senza interpretare HTTP 200 come successo                                  |

I conteggi non sono totali dell'intero database. I preset elaborati usano
finestre di 1000 righe, ranking locale e pagine senza riempimento da finestre
successive. Una release non esaminata può esistere altrove. Violarr usa uno
**snapshot ICVDB locale**, che può differire da contenuti più recenti o percorsi
di ricerca live dell'addon Stremio. Non effettua scraping né confronti live con
Stremio.

Se il database non è disponibile o è in sostituzione, attendi e riprova. Un
errore di fase mantiene solo i dati osservati; un errore HTTP può non produrre
alcun report.

### Esecuzioni delle strategie e provenienza

Il report v2 elenca ogni esecuzione osservata con stato, durata e numero di
candidati. Un'esecuzione parziale o non riuscita non dimostra l'assenza di
release corrispondenti. Il motore attuale usa ancora un solo ramo di query
esistente per finestra esaminata: il formato del report non aggiunge strategie e
non amplia i risultati.

**Provenienza dei risultati** raggruppa le osservazioni tramite un'identità
opaca SHA256 derivata dall'info-hash memorizzato. L'identità resta stabile tra
ordinamenti e finestre, mentre gli ID delle singole occorrenze mantengono
distinte le decisioni di filtro e paginazione delle righe duplicate. I conteggi
includono le occorrenze ripetute, non solo le identità uniche. Gli
identificatori delle strategie e le evidenze categoriali descrivono le fonti
osservate, non l'accettazione a valle.

Contributo univoco, deduplicazione, rilevanza e inclusione a livello di identità
mostrano **Non disponibile** (`null`) se non osservati esplicitamente. Il motore
attuale non deduplica, non fonde risultati e non calcola la rilevanza di
ricerca; il punteggio di elaborazione resta quello separato di preset/regole.
Un'identità ripetuta non dimostra che sia avvenuta una deduplicazione.

## Osservare le richieste dei client

1. Apri **Richieste** e abilita **Monitora le ricerche in ingresso**.
2. Avvia una ricerca dal tuo client attraverso Prowlarr.
3. Premi **Aggiorna**, oppure attendi l'aggiornamento ogni cinque secondi mentre
   questa vista è aperta.
4. Premi **Esamina richiesta** per vedere parametri originali/normalizzati,
   strategia, conteggi, durata, stato HTTP, fasi ed errori.
5. Premi **Riesegui richiesta** per una **nuova ricerca diagnostica** con i
   parametri originali. Database e impostazioni attuali possono differire dallo
   stato storico. La riesecuzione non viene aggiunta alla cronologia.

Il monitoraggio è disabilitato per impostazione predefinita. Conserva al massimo
100 richieste Torznab `/api` recenti in memoria, non i set di release; le più
vecchie vengono eliminate. Disabilitarlo ferma le nuove registrazioni ma
conserva la cronologia; **Svuota cronologia** la elimina. Riavviare Violarr
azzera stato e cronologia. Richieste oscurate, troncate o non valide non sono
rieseguibili.

Per salvare l'intero buffer attuale, premi **Esporta cronologia** accanto a
**Svuota cronologia**. Violarr recupera tutte le richieste registrate in quel
momento (fino a 100), non solo quella esaminata. Leggi **Anteprima cronologia**
e l'avviso sulla privacy, poi conferma con **Scarica JSON**. Il file locale
`violarr-request-history-v1.json` contiene versione di esportazione `1`, data e
ora di generazione e, per ogni richiesta, parametri originali/normalizzati
sanificati, strategia, fasi, conteggi, tempi, data e ora, stato HTTP, errori e
indicatori di troncamento/riesecuzione. Non include set di release o richieste
già eliminate dalla memoria. L'esportazione è disabilitata con cronologia vuota
o un'operazione sulla cronologia in corso. Si applicano l'oscuramento e il
limite di 4 MB descritti sotto; i termini di ricerca possono comunque essere
sensibili.

## Condividere un report in sicurezza

1. Dopo una ricerca, premi **Esporta report**.
2. Leggi **Anteprima report**: è il JSON che verrà scaricato, con versione
   report `2`, versioni applicazione/snapshot, parametri sanificati,
   impostazioni, fasi, finestre, conteggi, esecuzioni delle strategie,
   provenienza, dettagli dei candidati ed errori sicuri. Il browser permette
   anche di esportare report precedenti v1.
3. Premi **Scarica JSON**, poi rimuovi eventuali dati privati prima di allegarlo
   a una segnalazione. Il download è locale e non invia dati a servizi esterni.

L'esportazione esclude credenziali, header, magnet completi, SQL e
configurazione interna, limita dettagli e testo e rifiuta report oltre 4 MB.
L'oscuramento strutturale **non rende anonimi** titoli, ricerche o valori delle
regole. Il report può essere parziale se sono segnalati errori o troncamenti;
senza report il pulsante di esportazione è disabilitato.

::: warning Rete affidabile e privacy

WebUI, WebAPI e Torznab non hanno autenticazione. Chi può raggiungere Violarr
può eseguire diagnosi, abilitare il monitoraggio e leggere o cancellare la
cronologia. Esponi la porta 8000 solo su una rete affidabile o dietro un proxy
autenticato. Disabilita il monitoraggio e svuota la cronologia quando hai
terminato.

:::
