# Ebba, hushållstjänster och EutherVault

Inventering 2026-09-16. Detta är arkitektur och återställningsunderlag, inte en
bekräftelse på att alla nämnda kataloger ingår i backup. Inga lösenord, token,
kartor eller råa flashdumpar ska checkas in här.

## Maskiner och tjänster

| Plats | Del | Ansvar |
|---|---|---|
| 192.168.32.120 | Ebba, Dreame D10 Plus / r2205 | Original navigering, sensorer och motorskydd; lokal SSH, MIoT, kartor och högtalare |
| 192.168.32.186 | EutherWash, user `eutherwash.service`, TCP 8801 | Autentiserad lokal styrning/status, kartåtkomst, budleverans, tal och musik |
| .186 | `eutherwash-vacuum-map-sync.timer/service` | Import av validerade kartarkiv; behåller föregående arkiv vid fel |
| .186 | EutherShould, user `euthershould.service` | Läser lokal robotstatus genom EutherWash |
| .88 / Apansson | EutherVox, user `euthervox-gateway.service`, TCP 8788 | Autentiserad WSS för Android, kommandon och ljud; vidarebefordrar till EutherWash |
| .186 | EutherOxide | Apps/APK-distribution, serverkarta och scoped EutherID-inloggning för EutherVault |
| .186 | EutherNet, TCP 8791 | Inventering, driftstatus och denna karta |
| .149 | EutherVault, `https://eutherbackup.local` | Separat backupserver; åtkomst och faktisk datatäckning behöver verifieras |

Kartans tjänststatus kommer från systemd-snapshoten. `configured` för roboten
eller Vox är topologi, inte ett aktuellt nätverkstest. EutherNet väcker inte
roboten för att rita kartan.

## Vägen från originalrobot till lokal drift

Moderkortet är märkt R2205-MB-A1. UART gav bootloggar men ingen enkel,
bekräftad väg till beständig root. SPI NAND lästes därför ut med en egenbyggd
adapter. Flera läsningar, sidjämförelser och arbete med ECC/OOB och filsystem
behövdes; en programmerares lyckade skrivning bevisar inte att roboten bootar.
Originaldumpar och rekonstruktionsbevis är oersättliga privata artefakter.

Filsystem och senare robotens egen Linux 4.9.118-kärna kördes i labb. Det
verifierade programflöden, inte robotkortets hårdvara eller lödning. Efter
återmontering hittades en saknad ferritpärla som bryggades. Fysisk start,
UART/root och därefter automatisk SSH verifierades. Ingen fortsatt fjärrdrift
ska behöva chipavlödning.

Den senare verifierade nätverksimagen använder boot2/rootfs2. Rootfs1
behölls som återställningsväg. Endast nödvändiga startändringar och bootval
gjordes; följ den kronologiska källdokumentationen vid återställning, inte
äldre anteckningar om tidigare aktiv slot.

Robotens `/data/euther-network/guard.sh` med flaggan
`local-only.enabled` lägger två IPv4-blackhole-rutter, medan direkt LAN-trafik
fortsätter. Automatisk start och WAN-spärr verifierades efter fysisk omstart.
Det är en routingpolicy med uppstartsvillkor, inte garanti om noll trafik
under varje ögonblick av uppstarten. ZTE behövs inte för denna policy.

## Lokal karta och interaktion

EutherWash använder lokal MIoT (UDP 54321) och SSH (TCP 22). Beständiga
kartdata ligger under `/data/map/<id>/fine.bin`; tillfälliga exporter finns
under `/tmp`. Budkörningen använder robotens ursprungliga navigering och
stannar före tal. Leverans kan följas av hemgång eller väntan på plats.
Tidsgränser: körning 300 s, hemgång 180 s, watchdog 660 s.

Vox beta32 innehåller kartval/zoom, textleverans, direktknappen
**Säg med Ebbas röst här**, håll-in-tal från telefonen och musik. Direkt tal
behöver varken målpunkt eller körning. TTS genereras via serverkedjan och
ljudet levereras till robotens spelare över SSH. Push-to-talk använder
telefonens mikrofon; fungerande mikrofon i roboten är inte bekräftad.

Musik hämtas/avkodas i serverkedjan och strömmas till högtalaren. Robotens
egna meddelanden och interaktivt tal har prioritet. En tillfällig bind-mount
för ljudprioritet återställs vid omstart och installeras vid nästa musikstart;
ingen ny flashning krävs för detta. yt-dlp behöver sitt default/EJS-stöd för
de verifierade YouTube-länkarna. Musik kan fortsätta när appen stängs.

### Känd begränsning vid inventeringen

Kartarkivets synktjänst rapporterar
`map_sync_failed_previous_archive_preserved`. Efter omstart kan tillfälliga
exportfiler saknas trots att beständiga kartor och lokal navigering fungerar.
Det senaste validerade arkivet bevaras med dess verkliga ålder. Visa inte en
gammal bild som ny eller tolka detta ensamt som att roboten saknar karta.
Ny export efter kallstart behöver fortfarande följas upp. Ingen robotkörning
eller ljuduppspelning gjordes för denna inventering.

## EutherVault: vad som faktiskt är verifierat

EutherVault Browser och dess utloggade auth-status svarade över HTTPS på
`eutherbackup.local` (.149). Detta verifierar webbåtkomst, inte backupinnehåll.
SSH med den tillgängliga nyckeln nekades. Inloggad datainventering,
backupscheman, senaste lyckade jobb, retention och återläsning återstår.

De separata äldre backupjobben på .186 rapporterade däremot godkända
kontrollsummor och aktiva timers för `accounts`, `state` och `media`.
Senaste respektive filer var daterade 2026-09-16; totalt 189 krypterade
artefakter rapporterades. Health-data är en snapshot, inte en full restore.
Det finns också en separat krypterad spegel på .88 och EutherID-backup.
Dessa får inte likställas med EutherVault eller tas som bevis att Ebbas
privata filer ingår. Serverkartan skiljer nu dessa system åt och tar bort
äldre obelagda kopplingar till hela bibliotek och /srv.

### Täckning som måste kontrolleras i EutherVault

| Data | Källa | Täckning i EutherVault |
|---|---|---|
| Original NAND, rekonstruktion, skriv-/återläsningsbevis | .88: `~/.local/state/eutherwash/flash-research/`, `~/EutherWash/.private/` | Ej verifierad; högsta prioritet |
| Oberoende backup före lokal nätverksimage | .88: `~/.local/share/eutherwash/backups/local-network-before-20260915/` | Ej verifierad |
| Robotens kartor och beständiga konfiguration | Ebba: `/data` inklusive `/data/map` och nätverkspolicy | Ej verifierad |
| Robotcredential och known-hosts | .186: `~/.local/share/eutherwash/private/` | Ej verifierad; privat/krypterad backup |
| EutherWash-konfiguration och lokala kartarkiv | .186: `~/.config/eutherwash/`, `~/.local/state/eutherwash/` | Ej verifierad |
| Vox-konfiguration, TTS-referenser och privata media | .88: inventera faktiska tjänstreferenser före urval | Ej verifierad |
| Kod, tester och dokumentation | EutherWash, EutherVox, EutherNet och EutherOxide Git | Kan återhämtas från Git efter push; ersätter inte data ovan |

Nästa steg när EutherVault-åtkomst finns: jämför datasetens verkliga
inkluderings-/exkluderingsregler med tabellen, kontrollera senaste fullföljda
jobb och provåterläs en fil från varje kritiskt dataset till en separat
katalog. Jämför SHA-256 med källan. Ändra inte källfiler eller robotflash vid
restoretest. Redovisa täckning först efter detta.

## Källdokumentation

I syskonrepot EutherWash under `docs/`:

- `dreame-r2205-uart-checkpoint-2026-09-11.md`
- `dreame-r2205-rootfs-analysis-2026-09-12.md`
- `dreame-original-kernel-lab-2026-09-14.md`
- `dreame-physical-boot-2026-09-15.md`
- `dreame-local-cloud-replacement-2026-09-15.md` (senare avsnitt anger aktuell slot och WAN-policy)
- `ebba-navigation-analysis-2026-09-16.md`
- `ebba-live-talk-beta30-2026-09-16.md`
- `ebba-music-beta31-2026-09-16.md`
- `ebba-direct-speech-beta32-2026-09-16.md`

EutherOxides `docs/EUTHERVAULT_ADMIN.md` beskriver den äldre hostbackupytan.
Namnet ensamt bevisar inte integration med den separata servern .149.

## Tillfällig separat Ebba-backup på .186

På användarens begäran skapades en separat engångskopia under
`/srv/backups/ebba/20260916T192347Z/`. Den är inte bevis på att EutherVault
hämtar denna katalog; ingen EutherVault-inloggning eller konfiguration ändrades.

- `ebba-workstation.tar.zst`: hela `EutherWash/.private/`,
  `~/.local/state/eutherwash/flash-research/`,
  `~/.local/share/eutherwash/backups/` samt EutherWash docs, tools, src och
  tillgängliga projekt-/beroendefiler från .88.
- `manifest.json`: individuella SHA-256, storlekar och symlänkmål för 19 748
  filer/länkar, totalt 10 109 951 265 byte filinnehåll före komprimering.
- `ebba-server-runtime.tar.zst`: .186:s `.config/eutherwash/`,
  `.local/share/eutherwash/`, `.local/state/eutherwash/` och installerade
  EutherWash user units. Innehåller privata anslutningsuppgifter och kartcache.
- `SHA256SUMS`, `README.txt` och `server-runtime-sources.json`: verifiering,
  återläsningsanvisning och serverkällor.

Alla filinnehåll i arbetsstationsarkivet jämfördes mot manifestet före
överföring. På mottagaren verifierades arkiv- och manifest-SHA-256 samt zstd:s
integritetskontroll. Katalogen är endast åtkomlig för `nichlas` (0700), filerna
0600. Arkiven är inte krypterade: detta är en privat temporär backup på den
betrodda servern, inte en offentlig nedladdning eller Git-innehåll.

Roboten kontaktades inte. Kartor och robotpartitioner i kopian är tidigare
sparade versioner, inte en ny avläsning av robotens aktuella `/data`.
Serverns runtime är en live filkopia, inte en transaktionskonsistent
applikationssnapshot. Ursprungliga filer behölls på .88.

Verifiera från backupkatalogen med `sha256sum -c SHA256SUMS`. Återläs alltid
till en ny tom katalog med `tar --zstd -xf ARKIV -C ÅTERLÄSNINGSKATALOG`.
Detta återställer filer; det är inte en instruktion att skriva rådumpen direkt
till robotens NAND. Följ den inkluderade modell- och flashdokumentationen.
