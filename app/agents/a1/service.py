import logging
from typing import Optional
from uuid import UUID
from app.agents.a1.scraper import scraper
from app.agents.a1.enricher import enrich_property
from app.models.property import Property
from app.models.profile import UserProfile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, case, literal_column
# `func.greatest` removed: SQLite no tiene GREATEST nativo; usamos case()
from sqlalchemy.sql.expression import nullslast

logger = logging.getLogger(__name__)


class AgentA1Service:

    async def run_scraping(self, db: AsyncSession = None) -> int:
        """
        Raspa propiedades reales de InfoCasas.com.pe (Trujillo).

        Estrategia anti-timeout:
        1. Scraping completo SIN sesión BD (httpx, puede tardar 30-60s)
        2. Enriquecimiento IA SIN sesión BD (OpenRouter, puede tardar 60s+)
        3. Abrir sesión BD SOLO para insert — corta, sin idle
        Inserta en lotes de 10 para evitar INSERT masivo.
        """
        from app.core.database import AsyncSessionLocal

        # ── 1. Scraping ────────────────────────────────────────────────
        real_props = await scraper.scrape_all()
        if not real_props:
            logger.warning("Scraping retornó 0 propiedades — sitio posiblemente no disponible")
            return 0

        # Dedup por source_url
        seen_urls: set = set()
        unique_props: list = []
        for p in real_props:
            url = p.get("source_url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_props.append(p)

        # ── 2. Enriquecimiento IA ──────────────────────────────────────
        enriched_props: list = []
        for p in unique_props:
            try:
                ep = await enrich_property(dict(p))
            except Exception:
                ep = dict(p)
            enriched_props.append(ep)

        # ── 3. Insert en sesión fresca (mínimo tiempo idle) ───────────
        async with AsyncSessionLocal() as session:
            existing = await session.execute(select(Property.source_url))
            existing_urls = {row[0] for row in existing.fetchall()}

            new_props = [p for p in enriched_props if p.get("source_url") not in existing_urls]
            logger.info(f"Scraping: {len(real_props)} scrapeadas → {len(new_props)} nuevas")

            col_names = Property.__table__.columns.keys()
            inserted = 0
            BATCH = 10

            for i in range(0, len(new_props), BATCH):
                batch = new_props[i: i + BATCH]
                for p in batch:
                    filtered = {k: v for k, v in p.items() if k in col_names}
                    session.add(Property(**filtered))
                try:
                    await session.commit()
                    inserted += len(batch)
                    logger.info(f"Lote {i//BATCH + 1}: {len(batch)} propiedades insertadas")
                except Exception as e:
                    logger.error(f"Lote {i//BATCH + 1} falló: {e}")
                    await session.rollback()

        return inserted

    async def get_filtered_properties(
        self,
        db: AsyncSession,
        user_id: Optional[UUID] = None,
        district: Optional[str] = None,
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
        property_type: Optional[str] = None,
        listing_type: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list, int, list[str], str | None]:
        """
        Returns (items, total, relaxed_filters, suggestion).

        Cascade de 4 tiers — siempre retorna resultados si hay al menos
        una propiedad activa en la BD:

          tier 0: full filter (district + property_type + price_max)
          tier 1: relaja district
          tier 2: relaja district + property_type
          tier 3: relaja district + property_type + price_max
          tier 4: relaja listing_type (alquiler→venta o viceversa)

        SEMÁNTICA DE PRECIOS (modelo peruano):
          - price_max: HARD CAP (precio > max = NUNCA se muestra)
          - price_min: REFERENCIAL (no filtra; precios menores son MEJOR
            match porque el usuario tiene dinero de sobra)

        Selección de tier: en lugar de "primer tier con count > 0", se
        elige el tier cuyo **mejor resultado** tiene el mayor similarity
        score. Empate → tier más restringido (menor índice).

        Score combinado (0-1):
          - 0.40 si property_type coincide
          - 0.30 si district/zone coincide
          - 0.30 piecewise: full si price <= price_min, decay lineal hasta
            0 en price_max
        """
        purpose: Optional[str] = None

        # ── 1. Cargar perfil A3 ────────────────────────────────────────
        if user_id:
            profile_result = await db.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            profile = profile_result.scalar_one_or_none()
            if profile and profile.preferences:
                prefs = profile.preferences
                purpose = prefs.get("purpose")
                if district is None and prefs.get("zone"):
                    district = prefs.get("zone")
                if property_type is None and prefs.get("property_type"):
                    property_type = prefs.get("property_type")
                if price_max is None and prefs.get("price_max") is not None:
                    price_max = prefs.get("price_max")
                if price_min is None and prefs.get("price_min") is not None:
                    price_min = prefs.get("price_min")

        # ── 2. Determinar listing_type efectivo ───────────────────────
        effective_listing_type = listing_type
        if effective_listing_type is None and purpose:
            effective_listing_type = "rent" if purpose == "alquiler" else "sale"

        # Filtros base que SIEMPRE se aplican
        base = [Property.is_active == True, Property.status != "sold"]

        # ── 3. Construir 5 tiers de la cascade ─────────────────────────
        # IMPORTANTE: price_min NO se pasa a los tiers (es referencial).
        # Solo price_max filtra.
        tier_filters = []

        # tier 0: full filter
        tier_filters.append(self._build_tier(base, effective_listing_type,
                                              district, property_type, price_max))
        # tier 1: relaja district
        tier_filters.append(self._build_tier(base, effective_listing_type,
                                              None, property_type, price_max))
        # tier 2: relaja district + property_type
        tier_filters.append(self._build_tier(base, effective_listing_type,
                                              None, None, price_max))
        # tier 3: relaja district + property_type + price
        tier_filters.append(self._build_tier(base, effective_listing_type,
                                              None, None, None))
        # tier 4: relaja listing_type también (alquiler→venta o viceversa)
        if effective_listing_type in ("sale", "rent"):
            tier_filters.append(base + [Property.listing_type != effective_listing_type])
        else:
            tier_filters.append(base)

        # ── 4. Elegir el tier con el mejor top-score de similitud ──────
        score_expr = self._build_similarity_score_expr(
            district, property_type, price_min, price_max,
        )

        chosen_tier: Optional[int] = None
        chosen_filters: list = []
        chosen_top_score: float = -1.0
        total = 0

        for i, filters in enumerate(tier_filters):
            if score_expr is not None:
                # Query: COUNT + MAX(score) en una sola pasada
                q = select(
                    func.count().label("c"),
                    func.coalesce(func.max(score_expr), 0.0).label("top"),
                ).where(and_(*filters))
                row = (await db.execute(q)).one()
            else:
                # Sin score posible (sin district/type/price): solo count
                count = (await db.execute(
                    select(func.count()).select_from(Property).where(and_(*filters))
                )).scalar() or 0
                row = (count, 0.0)

            cnt = row[0] or 0
            top = float(row[1] or 0.0)

            if cnt > 0 and (
                top > chosen_top_score
                or (top == chosen_top_score and (chosen_tier is None or i < chosen_tier))
            ):
                chosen_tier = i
                chosen_filters = filters
                chosen_top_score = top
                total = cnt

        if chosen_tier is None:
            return [], 0, [], None

        # ── 5. Calcular filtros relajados y suggestion ─────────────────
        relaxed = self._compute_relaxed(
            chosen_tier, district, property_type,
            price_max, effective_listing_type,
        )
        suggestion = self._build_suggestion(relaxed, total)

        logger.info(
            f"A1 cascade: tier={chosen_tier}, top_score={chosen_top_score:.3f}, "
            f"filters={relaxed or 'exact'}, listing={effective_listing_type}, total={total}"
        )

        # ── 6. Construir ORDER BY (similitud al perfil) ───────────────
        order_clauses = self._build_similarity_order(
            district=district,
            property_type=property_type,
            price_min=price_min,
            price_max=price_max,
        )

        # ── 7. Query con paginación ───────────────────────────────────
        q = (
            select(Property)
            .where(and_(*chosen_filters))
            .order_by(*order_clauses)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        result = await db.execute(q)
        items = result.scalars().all()
        return items, total, relaxed, suggestion

    # ──────────────────────────────────────────────────────────────────
    # Helpers privados
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_tier(
        base: list,
        listing_type: Optional[str],
        district: Optional[str],
        property_type: Optional[str],
        price_max: Optional[float],
    ) -> list:
        """Construye la lista de filtros SQLAlchemy para un tier.

        price_min NO entra en filtros (es referencial).
        Solo price_max es hard cap.
        """
        filters = list(base)
        if listing_type in ("sale", "rent"):
            filters.append(Property.listing_type == listing_type)
        if district:
            filters.append(
                Property.district.ilike(f"%{district}%")
                | Property.zone.ilike(f"%{district}%")
            )
        if property_type:
            filters.append(Property.property_type.ilike(f"%{property_type}%"))
        if price_max is not None:
            filters.append(Property.price <= price_max)
        return filters

    @staticmethod
    def _compute_relaxed(
        tier: int,
        district: Optional[str],
        property_type: Optional[str],
        price_max: Optional[float],
        listing_type: Optional[str],
    ) -> list[str]:
        """Devuelve la lista de filtros que se cayeron en el tier elegido."""
        if tier == 0:
            return []
        relaxed: list[str] = []
        if tier >= 1 and district:
            relaxed.append("zone")
        if tier >= 2 and property_type:
            relaxed.append("property_type")
        if tier >= 3 and price_max is not None:
            relaxed.append("price")
        if tier >= 4 and listing_type in ("sale", "rent"):
            relaxed.append("listing_type")
        return relaxed

    @staticmethod
    def _build_suggestion(relaxed: list[str], total: int) -> Optional[str]:
        LABELS = {
            "zone": "zona",
            "property_type": "tipo de propiedad",
            "price": "presupuesto máximo",
            "listing_type": "tipo de operación (alquiler/venta)",
        }
        if not relaxed:
            return None
        if "listing_type" in relaxed and len(relaxed) == 1:
            return (
                f"No hay propiedades en alquiler; te mostramos las {total} ventas "
                f"más cercanas a tu perfil ordenadas por similitud."
            )
        parts = [LABELS.get(r, r) for r in relaxed]
        motivo = ", ".join(parts)
        return (
            f"Sin coincidencias exactas en {motivo}. Mostramos las {total} "
            f"propiedades más cercanas a tu perfil, ordenadas por similitud."
        )

    @staticmethod
    def _build_similarity_score_expr(
        district: Optional[str],
        property_type: Optional[str],
        price_min: Optional[float],
        price_max: Optional[float],
    ):
        """
        Score combinado (0-1) usado en MAX(score) y ORDER BY.
        Retorna una expresión SQLAlchemy o None si no hay nada que comparar.

          - 0.40 si property_type contiene el tipo del perfil
          - 0.30 si district o zone contiene la zona del perfil
          - 0.30 piecewise:
              prop.price <= price_min          → 0.30 (gran oferta)
              price_min < prop.price <= max    → decay lineal
              prop.price > max                → 0 (filtrado por cap)
        """
        has_type = bool(property_type)
        has_zone = bool(district)
        has_price_min = price_min is not None and price_min > 0
        has_price_max = price_max is not None and price_max > 0

        if not (has_type or has_zone or has_price_min or has_price_max):
            return None

        score = literal_column("0.0")

        if has_type:
            score = score + case(
                (Property.property_type.ilike(f"%{property_type}%"), 0.40),
                else_=0.0,
            )
        if has_zone:
            score = score + case(
                (
                    Property.district.ilike(f"%{district}%")
                    | Property.zone.ilike(f"%{district}%"),
                    0.30,
                ),
                else_=0.0,
            )

        if has_price_min and has_price_max and price_min < price_max:
            min_p = float(price_min)
            max_p = float(price_max)
            # piecewise: full 0.30 si price<=min, decay lineal hasta 0 en max
            #   0.30 * (max - price) / (max - min) cuando min < price <= max
            score = score + case(
                (Property.price <= literal_column(str(min_p)), 0.30),
                (
                    Property.price <= literal_column(str(max_p)),
                    literal_column(str(0.30))
                    * (literal_column(str(max_p)) - Property.price)
                    / literal_column(str(max_p - min_p)),
                ),
                else_=0.0,
            )
        elif has_price_max:
            max_p = float(price_max)
            # Solo price_max: inversamente proporcional (más bajo = mejor)
            # Implementado con case() para portabilidad SQLite (no GREATEST).
            # max(price, 1) portable:
            max_expr = case(
                (Property.price > literal_column("1"), Property.price),
                else_=literal_column("1"),
            )
            score = score + case(
                (Property.price > 0,
                 literal_column(str(0.30))
                 * literal_column(str(max_p))
                 / max_expr),
                else_=0.0,
            )
        elif has_price_min:
            min_p = float(price_min)
            # Solo price_min: full 0.30 si price <= min, 0 si price > min
            score = score + case(
                (Property.price <= literal_column(str(min_p)), 0.30),
                else_=0.0,
            )

        return score

    @staticmethod
    def _build_similarity_order(
        district: Optional[str],
        property_type: Optional[str],
        price_min: Optional[float],
        price_max: Optional[float],
    ) -> list:
        """Construye ORDER BY usando el mismo score que el MAX del cascade."""
        score_expr = AgentA1Service._build_similarity_score_expr(
            district, property_type, price_min, price_max,
        )
        if score_expr is None:
            return [nullslast(Property.price.asc())]
        return [score_expr.desc(), nullslast(Property.price.asc())]


agent_a1 = AgentA1Service()
