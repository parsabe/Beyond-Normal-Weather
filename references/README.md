# Inputs and related research

## Weather inputs

- **ERA5** hourly 10 m and 100 m wind speed and downward shortwave radiation, requested from Open-Meteo with `models=era5` and UTC timestamps. Radiation is the preceding-hour mean and the pilot groups it by its reported UTC timestamp. The ERA5 power series is an engineering proxy built from reanalysis. [Open-Meteo historical weather documentation](https://open-meteo.com/en/docs/historical-weather-api) · [Copernicus ERA5 time-series catalog](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels-timeseries)
- **CMIP6** daily `sfcWind` and `rsds` at the nearest native-grid point, with run-matched variables and native calendars. The pinned stores come from the Pangeo consolidated catalog. [Cloud access and catalog documentation](https://pangeo-data.github.io/pangeo-cmip6-cloud/accessing_data.html)

## Methods and comparison

- Cannon, Sobie, and Murdock (2015) introduced quantile delta mapping as a way of carrying modeled quantile changes through bias correction. This pipeline uses its additive form, then transfers local weather quantiles to daily capacity factors. [Paper](https://doi.org/10.1175/JCLI-D-14-00754.1)
- Vrac and Thao (2020) use analog rank resampling to address multivariate and temporal dependence. The hydrogen experiment uses a simpler two-variable block resampler tuned to delivery scores across tank sizes. [Paper and supplement](https://doi.org/10.5194/gmd-13-5367-2020)
- Lin et al. (2026) publish gridded flash-drought changes and test electricity dispatch impacts. Their source data and supplement support the fixed-definition wind and solar comparison on the benchmark branch. [Paper and source data](https://doi.org/10.1038/s41558-026-02753-3) · [Data and code archive](https://doi.org/10.5281/zenodo.21865561)
- Wu, Sun, and Craig (2026) model future green hydrogen costs and configurations. Their derived capacity-factor series are available by author request. Cost and storage design findings provide context for the hydrogen case. [Paper](https://doi.org/10.1016/j.xinn.2026.101303)
- Van der Most et al. (2024) study temporal compounding in European electricity systems. [Paper](https://doi.org/10.1038/s41560-024-01640-5)
