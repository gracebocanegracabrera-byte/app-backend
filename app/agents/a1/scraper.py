"""
Scraper A1 — propiedades reales de Trujillo (La Libertad).
Fuente: InfoCasas.com.pe

Resultados verificados por página:
- /inmuebles/trujillo/venta          → 21 cards (100% Trujillo)
- /inmuebles/trujillo/venta/pagina2  →  4 cards (100% Trujillo)
- /terrenos/trujillo/venta           → 16 cards (100% Trujillo)
- /inmuebles/trujillo/alquiler       → 10 cards (100% Trujillo)
- /inmuebles/la-libertad/venta       → 21 cards (100% Trujillo)
- /inmuebles/la-libertad/venta/pagina2 → 18 cards (~86% Trujillo)
- /inmuebles/la-libertad/alquiler    → 18 cards (~86% Trujillo)

Total antes de dedup: ~108 — esperado único: ~70-80
"""
import httpx
import re
import logging
from typing import Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

BASE = "https://www.infocasas.com.pe"

# URLs verificadas con propiedades reales de Trujillo
INFOCASAS_URLS = [
    # Ciudad Trujillo — 100% real
    (f"{BASE}/inmuebles/trujillo/venta",              "sale"),
    (f"{BASE}/inmuebles/trujillo/venta/pagina2",      "sale"),
    (f"{BASE}/terrenos/trujillo/venta",               "sale"),
    (f"{BASE}/inmuebles/trujillo/alquiler",           "rent"),
    # La Libertad (región) — filtramos por keywords de Trujillo
    (f"{BASE}/inmuebles/la-libertad/venta",           "sale"),
    (f"{BASE}/inmuebles/la-libertad/venta/pagina2",   "sale"),
    (f"{BASE}/inmuebles/la-libertad/alquiler",        "rent"),
]

# Distritos y zonas de Trujillo para filtrar propiedades no-Trujillo
TRUJILLO_KEYWORDS = {
    "trujillo", "moche", "victor larco", "víctor larco", "huanchaco",
    "la esperanza", "el porvenir", "florencia de mora", "salaverry",
    "laredo", "buenos aires", "virú", "viru", "panamericana norte",
    "california", "primavera", "soliluz", "santa inés", "santa ines",
    "la merced", "san andrés", "san andres", "orbegoso", "españa",
    "america sur", "el golf", "el recreo", "la libertad",
}


class PropertyScraper:

    async def scrape_infocasas(self) -> list[dict]:
        """
        Raspa InfoCasas.com.pe — fuente real de propiedades de Trujillo.
        Filtra automáticamente propiedades de otras ciudades.
        """
        all_props: list[dict] = []

        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            for url, listing_type in INFOCASAS_URLS:
                try:
                    resp = await client.get(url, headers=HEADERS)
                    if resp.status_code != 200:
                        logger.debug(f"InfoCasas {url} → HTTP {resp.status_code}")
                        continue

                    props = self._parse_infocasas(resp.text, listing_type)
                    logger.info(f"InfoCasas [{url.split('/')[-1]}] → {len(props)} propiedades")
                    all_props.extend(props)

                except Exception as e:
                    logger.warning(f"InfoCasas {url} falló: {e}")
                    continue

        return all_props

    def _is_trujillo(self, text: str) -> bool:
        """Devuelve True si el texto menciona Trujillo o distritos conocidos."""
        t = text.lower()
        return any(kw in t for kw in TRUJILLO_KEYWORDS)

    def _parse_infocasas(self, html: str, listing_type: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".listingCard")
        props = []

        for card in cards:
            try:
                text = card.get_text(" ", strip=True)
                if not self._is_trujillo(text):
                    continue  # Descartar propiedades de otras ciudades
                p = self._extract_card(card, listing_type)
                if p:
                    props.append(p)
            except Exception as e:
                logger.debug(f"Card parse error: {e}")
                continue

        return props

    def _extract_card(self, card, listing_type: str) -> Optional[dict]:
        # ── Imagen ───────────────────────────────────────────────────
        image_url = None
        for img in card.select("img"):
            src = img.get("data-src") or img.get("data-lazy") or img.get("src") or ""
            if src.startswith("http") and not src.endswith(".svg") and "loader" not in src:
                image_url = src
                break

        # ── Link / source_url ─────────────────────────────────────────
        link_el = card.select_one("a[href]")
        href = link_el.get("href", "") if link_el else ""
        if href.startswith("/"):
            source_url = f"{BASE}{href}"
        elif href.startswith("http"):
            source_url = href
        else:
            return None  # Sin URL no podemos deduplicar

        # ── Texto completo ────────────────────────────────────────────
        text = card.get_text(" ", strip=True)

        # ── Título ────────────────────────────────────────────────────
        title_el = card.select_one("h2.lc-title")
        if title_el:
            raw_title = title_el.get_text(strip=True)
        else:
            raw_title = card.select_one("h2, h3, h4, [class*=title]")
            raw_title = raw_title.get_text(strip=True) if raw_title else ""

        # Capitalizar y limpiar
        title = self._clean_title(raw_title)
        if not title or len(title) < 5:
            title = self._title_from_url(source_url)

        # ── Precio ───────────────────────────────────────────────────
        price = self._extract_price(card, text)

        # ── Tipo de propiedad ─────────────────────────────────────────
        # Usar el título como fuente principal para el tipo
        property_type = self._detect_type(title, text, source_url)

        # ── Distrito ─────────────────────────────────────────────────
        district = self._extract_district(text)

        # ── Zona ─────────────────────────────────────────────────────
        zone = self._detect_zone(district, text)

        # ── Área m² ───────────────────────────────────────────────────
        area = self._extract_area(text)

        # ── Dormitorios ───────────────────────────────────────────────
        bedrooms = self._extract_bedrooms(card, text)

        # ── Baños ─────────────────────────────────────────────────────
        bathrooms = self._extract_bathrooms(card, text)

        return {
            "title": title,
            "price": price,
            "image_url": image_url,
            "source_url": source_url,
            "source_name": "infocasas",
            "district": district,
            "zone": zone,
            "property_type": property_type,
            "area_m2": area,
            "bedrooms": bedrooms if property_type not in ("terreno", "local_comercial", "oficina") else None,
            "bathrooms": bathrooms,
            "listing_type": listing_type,
        }

    # ── Extracción ────────────────────────────────────────────────────

    def _extract_price(self, card, text: str) -> Optional[float]:
        # Preferir el elemento específico de precio
        price_el = card.select_one(".main-price, [class*=price], [class*=precio]")
        price_str = price_el.get_text(strip=True) if price_el else text

        # Formatos: "S/ 6.100.332" | "S/. 281,000" | "U$S 900.000" | "USD 120,000"
        match = re.search(
            r"(?:S/\.?\s*|U\$S\s*|USD\s*)([\d][.\d,\s]+)",
            price_str
        )
        if match:
            raw = re.sub(r"[^\d]", "", match.group(1))
            try:
                val = float(raw)
                # Sanitizar: precios absurdos (> 50M soles o < 100) → None
                if 100 <= val <= 50_000_000:
                    return val
            except Exception:
                pass
        return None

    def _extract_area(self, text: str) -> Optional[float]:
        """Extrae área en m². Maneja 'm²', 'm2', 'm  '."""
        match = re.search(r"([\d][.\d,]*)\s*m[²2\s]", text)
        if match:
            try:
                raw = match.group(1).replace(".", "").replace(",", ".")
                val = float(raw)
                if 1 <= val <= 500_000:
                    return val
            except Exception:
                pass
        return None

    def _extract_bedrooms(self, card, text: str) -> Optional[float]:
        # Buscar elemento específico primero
        dorm_el = card.select_one("[class*=bed], [class*=dorm], [class*=room], [class*=habitac]")
        if dorm_el:
            m = re.search(r"(\d+)", dorm_el.get_text())
            if m:
                return float(m.group(1))
        # Buscar en texto
        m = re.search(r"(\d+)\s*(?:dorm|Dorm|habitac|cuarto|ambiente)", text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if 1 <= val <= 10:
                return val
        return None

    def _extract_bathrooms(self, card, text: str) -> Optional[float]:
        bath_el = card.select_one("[class*=bath], [class*=bano], [class*=baño]")
        if bath_el:
            m = re.search(r"(\d+)", bath_el.get_text())
            if m:
                return float(m.group(1))
        m = re.search(r"(\d+)\s*[Bb]a[ñn]", text)
        if m:
            val = float(m.group(1))
            if 1 <= val <= 10:
                return val
        return None

    def _extract_district(self, text: str) -> str:
        # Patrón: "en Moche, Trujillo" | "en Victor Larco" | "trujillo"
        m = re.search(
            r"en\s+([A-Za-záéíóúÁÉÍÓÚñÑ\s]+?),?\s*(?:Trujillo|La Libertad)",
            text, re.IGNORECASE
        )
        if m:
            d = m.group(1).strip().title()
            if len(d) < 40:
                return d

        # Buscar distritos conocidos directamente
        text_lower = text.lower()
        for kw, display in [
            ("victor larco", "Victor Larco Herrera"),
            ("víctor larco", "Victor Larco Herrera"),
            ("huanchaco", "Huanchaco"),
            ("moche", "Moche"),
            ("la esperanza", "La Esperanza"),
            ("el porvenir", "El Porvenir"),
            ("florencia de mora", "Florencia de Mora"),
            ("salaverry", "Salaverry"),
            ("laredo", "Laredo"),
            ("buenos aires", "Buenos Aires"),
        ]:
            if kw in text_lower:
                return display

        return "Trujillo"

    def _detect_zone(self, district: str, text: str) -> str:
        d = district.lower()
        t = text.lower()
        if "victor larco" in d:
            return "Zona Golf" if "golf" in t else "Zona Residencial"
        if any(k in d for k in ("huanchaco", "buenos aires", "las delicias")):
            return "Zona Playera"
        if any(k in d for k in ("la esperanza", "florencia")):
            return "Zona Norte"
        if any(k in d for k in ("el porvenir", "laredo")):
            return "Zona Este"
        if any(k in d for k in ("moche", "salaverry")):
            return "Zona Sur"
        if "trujillo" in d:
            return "Trujillo Centro"
        return district

    def _detect_type(self, title: str, text: str, url: str) -> str:
        """Prioriza el título para detectar tipo — evita falsos positivos."""
        combined = title.lower()  # Solo título, más preciso
        full = (text + " " + url).lower()

        # Orden de prioridad: tipos específicos primero
        if any(k in combined for k in ("terreno", "lote", "parcela", "solar", "hectarea")):
            return "terreno"
        if any(k in combined for k in ("local", "comercial", "industrial", "deposito",
                                        "depósito", "hotel", "hostal", "almacen")):
            return "local_comercial"
        if any(k in combined for k in ("oficina", "consultorio", "coworking")):
            return "oficina"
        if any(k in combined for k in ("casa", "chalet", "villa", "duplex", "dúplex",
                                        "unifamiliar", "bungalow")):
            return "casa"
        if any(k in combined for k in ("depart", "dpto", "flat", "penthouse", "loft",
                                        "studio", "apartamento")):
            return "departamento"

        # Si no hay match en título, buscar en texto completo
        if any(k in full for k in ("terreno", "lote", "hectarea")):
            return "terreno"
        if any(k in full for k in ("local comercial", "local industrial")):
            return "local_comercial"
        if any(k in full for k in ("oficina", "consultorio")):
            return "oficina"
        if any(k in full for k in ("casa", "duplex", "dúplex")):
            return "casa"

        return "departamento"  # default

    def _clean_title(self, raw: str) -> str:
        """Capitaliza title slug y limpia caracteres raros."""
        if not raw:
            return ""
        # Capitalizar primera letra de cada palabra relevante
        cleaned = re.sub(r"\s+", " ", raw).strip()
        # Capitalizar
        words = cleaned.split()
        short = {"de", "en", "a", "el", "la", "los", "las", "del", "con", "y", "o", "e"}
        title_words = []
        for i, w in enumerate(words):
            if i == 0 or w.lower() not in short:
                title_words.append(w.capitalize())
            else:
                title_words.append(w.lower())
        return " ".join(title_words)[:120]

    def _title_from_url(self, url: str) -> str:
        """Genera título legible desde el slug de la URL."""
        slug = url.rstrip("/").split("/")[-1]
        # Quitar ID numérico al final
        slug = re.sub(r"/?\d+$", "", slug)
        slug = re.sub(r"[%\w]+-(\d+)$", r"\1", slug)
        # Quitar ID numérico del slug
        slug = re.sub(r"-\d+$", "", slug)
        # Reemplazar guiones por espacios
        words = slug.replace("-", " ").replace("_", " ").split()
        return " ".join(w.capitalize() for w in words if w)[:80]

    async def scrape_all(self) -> list[dict]:
        """Scraping completo — solo propiedades reales de Trujillo."""
        props = await self.scrape_infocasas()

        # Deduplicar por source_url
        seen: set = set()
        unique: list = []
        for p in props:
            u = p.get("source_url", "")
            if u and u not in seen:
                seen.add(u)
                unique.append(p)

        logger.info(f"Scraping final: {len(unique)} propiedades reales únicas de Trujillo")
        return unique


scraper = PropertyScraper()
