<div align="center">
  <h1>ailm</h1>
  <p><strong>Makineni izleyen, onemli olani sana soyleyen yapay zeka destekli Linux sistem asistani.</strong></p>
  <p>
    <img src="https://img.shields.io/badge/durum-v0.1--dev-blue" />
    <img src="https://img.shields.io/badge/platform-Linux-blue" />
    <img src="https://img.shields.io/badge/LLM-lokal--first-green" />
    <img src="https://img.shields.io/badge/lisans-MIT-lightgrey" />
    <img src="https://img.shields.io/badge/test-359%20ge%C3%A7ti-brightgreen" />
  </p>
  <p>
    <a href="README.md">English</a> ·
    <a href="ROADMAP.md">Yol Haritasi</a> ·
    <a href="VISION.md">Vizyon</a>
  </p>
</div>

---

## ailm nedir?

ailm, Linux icin bir sistem tepsisi daemon'idir. Makineni izler, sistem olaylarini lokal LLM ile siniflandirir ve her sabah bir ozet sunar — tamami kendi donaniminda calisir.

Sen sistemi izlemek yerine, **ailm senin icin izler.**

## Neden ailm?

Arch, CachyOS, EndeavourOS gibi rolling release dagitimlar gucludur ama gurultulurdur. Her gun guncellemeler, servis hatalari, disk baskisi, kernel degisiklikleri gelir.

ailm ikisi arasinda durur: gurultuyu okur, baglami LLM ile anlar ve yalnizca gercekten onemli olani sade bir dille iletir.

## v0.1 Ozellikleri

- Sistem tepsisi ikonu (yesil/sari/kirmizi saglik durumu)
- LLM ile siniflandirilmis olay akisi (popup feed)
- Sabah ozeti (her gun 06:00'da)
- journald log izleme + regex on-filtre + LLM siniflandirma
- Paket guncelleme takibi (pacman ALPM log parser)
- Snapshot olay izleme (snapper/snap-pac)
- Disk kullanimi uyarilari
- Basarisiz systemd servis tespiti
- Yeniden baslatma tespiti (kernel uyumsuzlugu)
- Guvenli eylem whitelist'i (servis yeniden baslatma, journal temizleme)
- pluggy hook sistemi
- LLM cikti dogrulama (evidence format)
- Zarif bozulma (LLM kuyrugu + saglik kontrolu)
- Kontrol paneli tray (baslat/durdur, model degistir)

## Mimari

```
Kaynaklar (6)              EventBus              Tuketiciler
· Disk monitoru    ──┐                    ┌──► DB kayit
· Servis monitoru  ──┤                    ├──► StatusTracker
· Pacman watcher   ──┼──► publish ──────► ├──► Hook sistemi
· Snapshot watcher ──┤                    ├──► LLM siniflandirma
· Reboot checker   ──┤                    └──► UI feed
· Journald reader  ──┘
                              │
                        SQLite WAL + Ollama
```

## Hizli Baslangic

```bash
# Gereksinimler: Python 3.12+, Ollama, Linux (Arch tabanli onerilir)

git clone https://github.com/mahsumaktas/ailm
cd ailm
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Sistem bagimliligini kur (Arch/CachyOS)
sudo pacman -S python-systemd

# LLM modeli indir
ollama pull qwen3.5:9b

# Calistir
ailm --no-ui

# Veya systemd servisi olarak kur
cp contrib/ailm.service ~/.config/systemd/user/
systemctl --user enable --now ailm
```

## Kaynak Kullanimi

ailm gorunmez olmak icin tasarlandi:

| Metrik | Deger |
|--------|-------|
| CPU (bosta) | ~%0 (olay tabanli, surekli sorgulamaz) |
| RAM | ~35-40 MB |
| LLM cagrilari | Sadece siniflandirma + gunluk ozet icin |

## Lisans

MIT — bkz. [LICENSE](LICENSE).
