# CLAUDE.md — ailm Proje Talimatlari

## Proje
ailm — AI-powered Linux system companion. 20 event source, Jazari-4B LLM, 528 test.

## Gunluk Analiz Gorevi
Her calistirildiginda asagidaki adimlari takip et:

### 1. Sistem Durumu Kontrolu
```bash
systemctl --user status ailm
journalctl --user -u ailm --since "24 hours ago" --no-pager | tail -50
```

### 2. Event Analizi
```python
# ailm DB'den son 24 saati analiz et
import sqlite3
from pathlib import Path
db = sqlite3.connect(Path.home() / ".local/share/ailm/ailm.db")
# Source dagilimi, severity dagilimi, trend alerts, actionable events
# Gurultu orani: INFO event sayisi / toplam event sayisi
# LLM kullanim orani: summary != NULL / toplam
```

### 3. Performans Kontrolu
- GPU utilization (nvidia-smi) — %20 ustu surdurulebilir degil
- CPU sicaklik (sensors) — 75C ustu uyar
- RAM kullanimi (free -h) — %80 ustu uyar
- ailm'in kendi RAM kullanimi (ps)

### 4. Sorun Tespiti
- Tekrarlayan ayni event'ler (dedup calismiyor mu?)
- LLM'e gereksiz giden event'ler (INFO seviyesi gitmesin)
- Eksik yakalanmayan onemli event'ler
- Prefilter'da gereksiz pattern'ler

### 5. Iyilestirme
- Sorun bulursan KOD YAZ ve duzenle
- Test calistir: `cd /home/mahsum/ailm && .venv/bin/python -m pytest tests/ -q`
- Servisi restart et: `systemctl --user restart ailm`

## Teknik Bilgiler
- Python 3.12+, asyncio, PySide6
- Venv: /home/mahsum/ailm/.venv
- Config: ~/.config/ailm/config.toml
- DB: ~/.local/share/ailm/ailm.db
- LLM: Jazari-4B-SFT (Ollama, temperature=0)
- 20 source, 30s-86400s poll intervalleri
