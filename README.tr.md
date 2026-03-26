<div align="center">
  <h1>ailm</h1>
  <p><strong>Makineni izleyen, önemli olanı sana söyleyen yapay zeka destekli Linux sistem asistanı.</strong></p>
  <p>
    <img src="https://img.shields.io/badge/durum-pre--alpha-orange" />
    <img src="https://img.shields.io/badge/platform-Linux-blue" />
    <img src="https://img.shields.io/badge/LLM-lokal%20%7C%20bulut-green" />
    <img src="https://img.shields.io/badge/lisans-MIT-lightgrey" />
  </p>
  <p>
    <a href="README.md">English</a> ·
    <a href="ROADMAP.md">Yol Haritası</a> ·
    <a href="VISION.md">Vizyon</a>
  </p>
</div>

---

## ailm nedir?

ailm, Linux için bir sistem tepsisi daemon'ıdır. Makineni izler, alışkanlıklarını öğrenir
ve kendi donanımında çalışan lokal bir LLM ile sana sabah özeti sunar.

Sen sistemi izlemek yerine, **ailm senin için izler.**

## Neden ailm?

Arch, CachyOS, EndeavourOS gibi rolling release dağıtımlar güçlüdür ama gürültülüdür.
Her gün güncellemeler, .pacnew dosyaları, servis hataları, disk baskısı, kernel değişiklikleri gelir.
Çoğu kullanıcı ya bunları görmezden gelir ya da çok zaman harcayarak takip eder.

ailm ikisi arasında durur: gürültüyü okur, bağlamı anlar ve yalnızca gerçekten önemli olanı
sana sade bir dille iletir.

## Temel Felsefe

- **Proaktif, reaktif değil.** ailm sormadan söyler.
- **Varsayılan olarak lokal.** Bulut LLM seçmediğin sürece logların makineni terk etmez.
- **Dinleyici, yapıcı değil.** ailm mevcut araçları okur (snapper, pacman, systemd) — onların yerini almaz.
- **Zamanla öğrenir.** Ne kadar kullanırsan, o kadar az gürültü görürsün.
- **Zarif bozulma.** Ollama kapansa bile ailm çalışmaya devam eder — olaylar kuyruğa girer, analiz bekler.

## Mimari Özet

```
journald ──┐
pacman  ───┤──► Event Bus ──► SQLite + Vector DB ──► LLM ──► Feed / Tray / Bildirim
snapper ───┘                        │
                              Hafıza Sistemi
                    (episodik + semantik + prosedürel)
```

## Kurulum

> ⚠️ ailm pre-alpha aşamasındadır. Henüz paket yöneticisiyle kurulum desteklenmiyor.

```bash
git clone https://github.com/yourusername/ailm
cd ailm
pip install -e ".[dev]"
ollama pull qwen3.5:9b
ollama pull nomic-embed-text
ailm
```

## Lisans

MIT
