# DROPS-Hydro MX v0.5.4

Abra Guia_inundacion.html para las unidades de profundidad, el cálculo de lluvia excedente, Manning variable, archivos y límites de validación. La guía se puede abrir desde la pestaña Inundación y se copia en cada escenario. El escenario sintético de prueba mantiene una alerta de control de volumen; no se presenta como validación hidráulica.

# DROPS-Hydro MX v0.5.3

Consulte NOVEDADES_0.5.3.md para CHIRPS, selección automática de recortes y CN sin suelo local obligatorio.

# DROPS-Hydro MX v0.5.2

Consulte NOVEDADES_0.5.2.md para la corrección del CRS de cuenca.

# DROPS-Hydro MX v0.5.1

Consulte NOVEDADES_0.5.1.md para recortes y CRS de cuenca.

# DROPS-Hydro MX v0.5.0

Consulte NOVEDADES_0.5.0.md para instalación, datos necesarios, métodos, pruebas y límites. Las notas anteriores se conservan como historial.

## v0.4.8 — corrección del núcleo de delimitación de cuencas
- Replica la secuencia robusta observada en ArcGeek: DEM de análisis → `r.fill.dir` → `r.watershed` → acumulación/dirección.
- La red se extrae con el mismo DEM acondicionado y la misma acumulación usados para producir el raster de dirección.
- `r.water.outlet` ya no recibe una región GRASS reimpuesta; usa directamente la grilla del raster de dirección.
- El MDE rápido se remuestrea con vecino más cercano para reducir cambios topológicos introducidos por interpolación.
- Salidas hidrológicas y cuencas usan nombres únicos por ejecución para evitar archivos antiguos/bloqueados en Windows.
- Diagnóstico nuevo: compara el área vectorial delimitada con el área esperada a partir de la acumulación en el outlet.
- Para esta versión es necesario recalcular solamente el **paso 3** y después regenerar la red; no es necesario volver a unir los DEM.

## v0.4.7
- Snap hidrológico del outlet al píxel de mayor acumulación dentro de la red raster.
- r.water.outlet usa exactamente la extensión y resolución del raster de dirección.
- Vectorización de cuenca mediante r.to.vect area con fallback a polygonize.
- Diagnóstico de acumulación, desplazamiento y área de la cuenca.

# DROPS-Hydro MX v0.4.6

Experimental QGIS plugin for watershed analysis and hydroclimatic extremes in Mexico.

**Developer:** Prof. Juan Antonio Torres Martinez  
**Institution:** Tecnologico de Monterrey  
**Research group:** Data-driven Research On aquatic Pollution and Sustainability (DROPS Research Group)  
**Contact:** antoniotorres@tec.mx · antoniotorres9350@gmail.com


## Fix in v0.4.2
- Replaced the Processing `gdal:merge` mosaic step with direct GDAL `BuildVRT` + `Translate`.
- Supports mixed GDAL-readable DEM formats (GeoTIFF, BIL/HDR, IMG, ASC, DEM) provided they share a CRS.
- Preserves the finest input resolution, creates a diagnostic VRT, materializes a tiled/compressed BigTIFF-safe GeoTIFF, and verifies that the final extent covers all input tiles before activating it.
- Reports number of tiles, raster dimensions, resolution and CRS after mosaicking.

## Main changes in v0.4.2

### 1. Faster DEM workflow
- One or multiple DEMs; optional mosaic.
- GeoTIFF, BIL (+ HDR), IMG, ASC and DEM inputs.
- Automatic UTM reprojection when needed.
- New **Auto fast** analysis resolution: keeps the working grid near 25 million cells for drainage-network/outlet screening while preserving the original DEM.
- D8/SFD, MFD, and disk-swap modes.
- Flow accumulation + direction only; avoids unnecessary GRASS outputs.
- Drainage line extraction from existing accumulation with `r.stream.extract` where available.
- Outlet selection snaps to drainage lines.

### 2. CHIRPS without waiting for the DEM
The CHIRPS module is now independent of watershed delineation. It can use:
- a selected basin (spatial average),
- the current map center, or
- manually entered longitude/latitude.

Default route: **ClimateSERV CHIRPS v2**, processed server-side and split automatically into <=20-year requests. The legacy/direct CHIRPS v3 RNL COG route is retained as an experimental slow option.

Outputs include the daily series CSV, annual maxima, Gumbel return-period rainfall, and design rainfall.

### 3. Drought module
- Monthly aggregation of the downloaded CHIRPS daily series.
- 1-, 3-, 6-, and 12-month empirical standardized precipitation screening index.
- CSV export and qualitative drought classes.
- This is an empirical normal-score index for screening; it is not a replacement for a formally fitted gamma-SPI/SPEI workflow.

### 4. AI / ML module
- **Adaptive drainage threshold:** starts from a hydrologic contributing-area rule; after >=3 user-validated cases, switches to a local k-NN recommender based on DEM resolution, grid size and relief.
- **CHIRPS bias correction:** dependency-free seasonal regression using CHIRPS, sqrt(CHIRPS), and annual sine/cosine terms; chronological 80/20 validation reports MAE, RMSE and R² and exports a corrected daily series.
- Avoids NumPy/scikit-learn to reduce Python-environment conflicts in QGIS on Windows.

### 5. Existing capabilities retained
- Basin morphometry.
- Tc comparison: Kirpich, Giandotti, Témez, NRCS/SCS lag, average and manual value.
- SCS-CN rainfall-runoff screening.
- Return-period peak-flow estimates.
- GRASS `r.sim.water` flood depth/discharge mapping.

## Recommended first test
To validate CHIRPS without waiting for hydrologic preprocessing:
1. Open **CHIRPS / extremos**.
2. Choose **Centro del mapa** or enter coordinates manually.
3. Keep **ClimateSERV CHIRPS v2 — rápido**.
4. Select a short period first (e.g., 2010–2020).
5. Click **Descargar CHIRPS...**.
6. Review `chirps_daily_climateserv.csv` and annual maxima.

For DEM processing, start with **Auto rápido** + **D8/SFD**. Once the outlet/basin is identified, retain the original high-resolution DEM for detailed analyses.

## Status
Experimental software for research, teaching and screening. Hydrologic design and hazard results require validation against local observations, official IDF information and appropriately calibrated hydraulic/hydrologic models before engineering use.


## v0.4.2 — corrección de red de drenaje
- `r.stream.extract` se usa para generar únicamente el raster de cauces.
- La red raster se convierte después explícitamente a líneas con `r.to.vect`.
- Se evita la salida vectorial mixta de GRASS (líneas + puntos), que podía abrirse en QGIS como una capa puntual.
- Se usan nombres únicos por ejecución para evitar bloqueos de GeoPackage en Windows.


## v0.4.3
- Fixes QGIS 3.44 / Windows access-violation crashes caused by `QCoreApplication.processEvents()` inside Processing feedback callbacks.
- Processing worker threads no longer update Qt widgets directly; the dialog polls progress safely with a GUI-thread `QTimer`.
- Fast DEM mode now creates a warped VRT instead of writing a full temporary GeoTIFF before flow analysis.

## v0.4.6 — selección de outlet y reanudación de sesiones
- La ventana del plugin ahora es **no modal** y persistente; el mapa de QGIS permanece interactivo mientras se selecciona el outlet.
- El botón **Seleccionar punto de salida** activa un `QgsMapToolEmitPoint` real sobre el canvas y ajusta el clic a la línea de drenaje más cercana.
- Cerrar y volver a abrir DROPS-Hydro durante la misma sesión ya no reinicia el flujo.
- Nueva sección **Reanudar / reutilizar resultados existentes** para elegir MDE, acumulación, dirección, red lineal y cuenca ya cargados en el proyecto.
- Botón **Recuperar automáticamente desde proyecto/carpeta**: busca productos existentes y no recalcula nada.
- Se guarda `.dropshydro_session.json` en la carpeta de trabajo, además de propiedades del proyecto, para poder continuar incluso tras reiniciar QGIS.
- Se recupera también el MDE temporal de análisis, importante cuando la dirección/acumulación se calcularon a resolución rápida.



## v0.4.6
- Native QGIS vertex/segment snapping for outlet selection, inspired by the ArcGeek workflow.
- Dedicated map tool based on QgsMapMouseEvent rather than only canvasClicked.
- Final outlet is always projected back to the drainage network.
- Existing-session reuse from project/work folder remains enabled.
