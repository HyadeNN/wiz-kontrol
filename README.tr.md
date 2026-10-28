<div align="center">

<img src="assets/icon.png" width="96" alt="WiZ Kontrol simgesi">

# WiZ Kontrol

**Philips WiZ akıllı ampuller için hızlı, yerel bir Windows uygulaması — doğrudan ev ağın üzerinden. Bulut yok, hesap yok, telefona gerek yok.**

[![CI](https://github.com/HyadeNN/wiz-kontrol/actions/workflows/ci.yml/badge.svg)](https://github.com/HyadeNN/wiz-kontrol/actions/workflows/ci.yml)
[![Son sürüm](https://img.shields.io/github/v/release/HyadeNN/wiz-kontrol?label=indir&color=f5b43c)](https://github.com/HyadeNN/wiz-kontrol/releases/latest)
![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d4)
[![Lisans: MIT](https://img.shields.io/badge/lisans-MIT-green)](LICENSE)

[**İndir**](https://github.com/HyadeNN/wiz-kontrol/releases/latest) · [Özellikler](#özellikler) · [Kurulum](#kurulum) · [Kaynaktan derleme](#kaynaktan-derleme) · [English](README.md)

<img src="docs/screenshots/hero.png" alt="Sahne, renk ve beyaz sekmeleri" width="100%">

</div>

---

## Neden?

WiZ'in Android ve iOS uygulamaları var, ama **Windows için resmi bir uygulaması yok**. Android uygulamalarını Windows'ta çalıştıran WSA da 2025'te kapatıldı. WiZ Kontrol bu boşluğu dolduruyor: görev çubuğundan ya da sistem tepsisinden aç, ışık anında tepki versin. Komutlar dünyanın öbür ucundaki bir sunucuya değil, **doğrudan Wi-Fi'daki ampule** gidiyor.

## Özellikler

| | |
|---|---|
| 💡 **Açma/kapama ve parlaklık** | Tek düğme, tek kaydırıcı (%10–100). Değişiklik milisaniyeler içinde. |
| 🌡️ **Ayarlanabilir beyaz** | Canlı renk gradyanlı 2200 K – 6500 K kaydırıcısı ve *Mum / Sıcak / Doğal / Gün ışığı / Soğuk* hazır ayarları. |
| 🎨 **Renk çarkı** | Ton + doygunluk çarkı ve hızlı renkler. Pastel tonlar ampulün soğuk beyaz kanalını kullanır; soluk değil temiz görünür. |
| ✨ **35 hazır sahne** | Okyanus, Şömine, Parti, Gün batımı, Noel… Her kutucuk, **sahnenin döngüdeki renklerinden, her rengin ekranda kalma süresiyle orantılı bir gradyan** olarak çizilir. Hız ayarlanabilir. |
| ⭐ **Favoriler** | O anki ışığı tek tıkla kaydet. Ad vermek isteğe bağlı; boş bırakırsan otomatik adlandırılır (ör. *Şömine*, *Sıcak 2700 K*, *Açık mavi*). |
| ⏰ **Programlar** | Gün gün seçilebilen günlük programlar: aç, kapat, favori uygula, **30 dakikalık gün doğumu uyanması** ya da 30 dakikada yavaşça kapatma. |
| 🌙 **Uyku zamanlayıcısı** | 15–120 dk; istersen ışık kapanana kadar yavaşça kısılır. |
| 🖥️ **Sistem tepsisi** | Saatin yanında durur. Sağ tıkla: aç/kapat, parlaklık, favoriler. Tepsi simgesi ampulün o anki rengini alır. |
| 🔄 **Hep senkron** | Işığı telefondan ya da sesli asistandan değiştirsen bile uygulama birkaç saniye içinde görür. |
| 🏠 **Birden çok ampul** | Ağdaki tüm WiZ ampulleri otomatik bulur; birini ya da hepsini birden kontrol et, istediğin gibi adlandır. |
| 🚀 **Windows ile başlat** | İsteğe bağlı; sessizce tepside açılır. Tek kopya çalışır: tekrar açınca mevcut pencere öne gelir. |
| 🌍 **10 dil** | Türkçe, English, Deutsch, Español, Français, Italiano, Português, Русский, 中文, 日本語. Windows diline göre otomatik seçilir, Ayar sekmesinden değiştirilebilir. Sahne adları da çevrilidir. |

## Kurulum

1. **Yerel iletişimi aç (bir kerelik):** WiZ uygulaması → Ayarlar → Güvenlik → **Yerel iletişime izin ver**. Bilgisayarın ampulle **aynı Wi-Fi ağında** olduğundan emin ol.
2. [Son sürümden](https://github.com/HyadeNN/wiz-kontrol/releases/latest) **`WiZ-Kontrol-vX.Y.Z.exe`** dosyasını indir ve çalıştır. Ampuller otomatik bulunur.

> [!TIP]
> Exe imzalı olmadığı için SmartScreen *"Windows bilgisayarınızı korudu"* diyebilir. **Ek bilgi → Yine de çalıştır**'a tıkla. İstersen [kaynaktan kendin derleyebilirsin](#kaynaktan-derleme).

Ayarlar, favoriler ve programlar `%APPDATA%\WizKontrol\config.json` içinde saklanır; güncellemelerde kaybolmaz.

### Yerelde çalışanlar ve bulut gerektirenler

| ✅ Bu uygulamayla, yerelde | ☁️ Hâlâ WiZ uygulaması gerekir |
|---|---|
| Aç/kapat, parlaklık, beyaz, renk, sahneler | Ev ağı dışından kontrol |
| Favoriler, programlar, uyku zamanlayıcısı | Alexa / Google Asistan entegrasyonu |
| Çoklu ampul, canlı senkron | Yazılım güncellemesi, yeni ampulü Wi-Fi'a ekleme |

WiZ Kontrol'deki programlar uygulama açıkken (tepsideyken de olur) çalışır.

## Kaynaktan derleme

Windows'ta **Python 3.10+** gerekir.

```bash
git clone https://github.com/HyadeNN/wiz-kontrol.git
```

```bash
cd wiz-kontrol
```

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m wizkontrol
```

Testler: `python -m pytest` · Lint: `python -m ruff check .` · Exe: `scripts\build.ps1` (çıktı: `dist\WiZ Kontrol.exe`).

Protokolün ayrıntıları ve proje yapısı için [İngilizce README](README.md#how-it-works)'ye bak.

## Notlar

- **Sahne gradyanları yaklaşıktır.** WiZ sahnelerin renk dizilerini ve sürelerini yayınlamıyor, ampul de bildirmiyor; [`scenes.py`](wizkontrol/scenes.py) içindeki paletler sahnelerin görünüşüne göre hazırlandı. Düzeltmelere açığız.
- WiZ RGB + ayarlanabilir beyaz ampulle (`ESP25_SHRGB_01`, yazılım 1.38) test edildi.

## Lisans ve uyarı

[MIT](LICENSE) © 2026 Hüseyin Efe Karagöz

Bu bağımsız bir projedir; **Signify veya Philips ile bağlantılı değildir, onlar tarafından desteklenmez.** WiZ ve Philips ilgili sahiplerinin ticari markalarıdır.
