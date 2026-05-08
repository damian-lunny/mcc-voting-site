# MCC voting raspberry

## Setup

* Copy ```photo-voting-system``` to your target directory
* Copy ```other-files/hostapd.conf``` to ```/etc/hostapd/```
* Update ```other-files/flaskapp.service``` to reflect correct target directory
* Copy ```flaskapp.service``` to ```/etc/systemd/system/```
* To enable the service, run

```
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl restart flaskapp.service
sudo systemctl status flaskapp.service
```

* For backups, add this to your crontab (at 20:15 on a Tuesday, create a copy of the voting.db and append the date):

```
15 20 * * 2 cp /home/dietpi/mcc-photo-voting/voting.db /home/dietpi/backups/voting-$(date +\%F).db
```

## Hostapd.conf - created by DietPi

> Copilot answer to why Android could not connect to the wireless set up by DietPi

Yes — there is a **clear, well‑known incompatibility** in that configuration that commonly causes **Android devices to refuse or struggle to connect**.

## The main problem (this is the big one)

### ❌ WPA2 + TKIP is effectively broken on modern devices

You have:

```ini
wpa=2
wpa_pairwise=TKIP
rsn_pairwise=CCMP
```

This combination is **not valid for modern WPA2 clients**, especially Android.

### Why this breaks Android connections

*   **WPA2 requires CCMP (AES)** for full compliance
*   **TKIP is deprecated and considered insecure**
*   **802.11n explicitly forbids TKIP**  
    If TKIP is enabled, the AP is supposed to fall back to legacy 802.11a/b/g rates
*   **Android (especially Android 8+) will often reject WPA2-PSK networks that advertise TKIP**, or repeatedly fail to authenticate

In short:

> **WPA2 + TKIP + 802.11n = client incompatibility**

Android is stricter than many laptops, which is why you see the issue there first.

***

## Secondary contributing factor

You also have:

```ini
ieee80211n=1
wmm_enabled=1
```

These are required for 802.11n, **but 802.11n requires CCMP only**.

If TKIP is present at all, many clients will:

*   refuse to associate
*   associate but fail DHCP
*   endlessly reconnect

***

## The correct, Android‑friendly fix ✅

Change your security section to **CCMP only**:

```ini
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
```

And **remove this line entirely**:

```ini
wpa_pairwise=TKIP
```

That’s it — no TKIP at all.

***

## Recommended full corrected config (minimal changes)

```ini
interface=wlan0
driver=nl80211
ssid=MCC-HotSpot
country_code=GB
hw_mode=g
channel=3
ieee80211n=1
wmm_enabled=1

auth_algs=1
ignore_broadcast_ssid=0

wpa=2
wpa_passphrase=mcchotspot
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
```

***

## Minor notes (not blockers, but worth knowing)

### Channel 3 (FYI)

Channel 3 is legal in GB but **overlaps heavily** with 1 and 6.  
It won’t break Android, but **channel 1 or 6** is usually cleaner.

### Raspberry Pi 3 limitations

*   2.4 GHz only
*   No 802.11ac/ax → your disabling of those is correct

***

## Summary

✅ **Root cause:**  
WPA2 + TKIP is rejected by modern Android devices, especially when 802.11n is enabled.

✅ **Solution:**  
Use **CCMP (AES) only**, remove TKIP entirely.

If you want, I can:

*   Tune it for **maximum Android compatibility**
*   Make it **WPA3‑transition compatible**
*   Diagnose with `hostapd` logs (`-dd`)

Just say the word.

