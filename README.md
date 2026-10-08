# DROPS-Hydro MX

**Experimental QGIS plugin for watershed analysis, hydroclimatic extremes and hydrological screening in Mexico.**

**Version:** 0.5.4  
**Developer:** Prof. Juan Antonio Torres Martínez  
**Research group:** DROPS Research Group — Data-driven Research On aquatic Pollution and Sustainability  
**Institution:** Tecnológico de Monterrey  
**Contact:** antoniotorres@tec.mx

## Overview

DROPS-Hydro MX provides a workflow for watershed delineation, terrain and drainage-network analysis, basin morphometry, CHIRPS-based precipitation analysis, design rainfall and flow estimates, flood-screening maps, drought screening, and experimental lightweight machine-learning utilities.

## Key capabilities

- DEM mosaicking and terrain preprocessing with GDAL and QGIS Processing.
- Drainage-network extraction, watershed outlet selection and basin delineation using GRASS GIS tools.
- Watershed morphometric indicators and time-of-concentration estimates.
- CHIRPS precipitation analysis and return-period estimates.
- SCS Curve Number runoff screening and preliminary peak-flow calculations.
- Flood-depth screening using GRASS `r.sim.water` and spatially variable Manning roughness.
- Empirical standardized precipitation screening and experimental data-driven functions.
- Reuse of outputs and restoration of prior analysis sessions.

## Installation

1. Download the ZIP attached to the [v0.5.4 release](../../releases/tag/v0.5.4) (once the release is published), or use the original plugin distribution ZIP.
2. Open **QGIS → Plugins → Manage and Install Plugins → Install from ZIP**.
3. Select the distribution archive and activate **DROPS-Hydro MX**.

The plugin declares **QGIS 3.28 or newer** as its minimum supported version. It relies on the QGIS Python environment and external Processing providers such as GDAL and GRASS GIS; make sure these providers are installed and enabled. Compatibility beyond the declared minimum has not been systematically verified.

**For developers:** clone the repository and place (or symlink) the `DROPSHydroMX/` directory in your QGIS user plugin directory. The repository root is not the plugin directory.

## Repository layout

```text
DROPS-Hydro-MX/
├── DROPSHydroMX/       # Installable QGIS plugin
│   ├── __init__.py
│   ├── metadata.txt
│   └── ...
├── docs/               # Technical notes, historical README and flood guide
├── .github/workflows/   # Static Python checks
└── README.md
```

## Documentation

- [Flood/inundation methodology and limitations](docs/Guia_inundacion.html)
- [Detailed development history](docs/DEVELOPMENT_HISTORY.md)
- [Version history](docs/NOVEDADES_0.5.3.md)

## Scientific-use notice

**Experimental research/teaching software.** Outputs are intended for exploratory and screening analyses, not as independently validated hydraulic hazard maps or engineering design results. Verify inputs, projection, scale, conservation diagnostics and method assumptions. Validate results with local observations, official intensity–duration–frequency information and appropriately calibrated models before decisions affecting infrastructure or safety.

## Contributing and reuse

Issues and suggestions are welcome. A license has **not yet been selected**, so public availability on GitHub does not itself grant permission to redistribute or modify the code. Add an appropriate `LICENSE` file after deciding the project's terms.
