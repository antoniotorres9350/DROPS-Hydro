"""Numerical models, independent of Qt and QGIS."""
import csv
import json
import math
import os


def export_csv(path, rows):
    if not rows: raise ValueError('No hay resultados para exportar.')
    os.makedirs(os.path.dirname(os.path.abspath(path)),exist_ok=True)
    keys=list(dict.fromkeys(key for row in rows for key in row))
    temporary=path+'.tmp'
    with open(temporary,'w',newline='',encoding='utf-8-sig') as file:
        writer=csv.DictWriter(file,fieldnames=keys); writer.writeheader(); writer.writerows(rows)
    os.replace(temporary,path)


def fit_distributions(values, periods):
    import numpy as np
    try: from scipy import stats
    except ImportError as exc: raise ValueError('Se necesita SciPy en el Python de QGIS para comparar distribuciones.') from exc
    x=np.asarray(values,dtype=float)
    if len(x)<10 or not np.isfinite(x).all() or (x<=0).any() or np.std(x)<=0:
        raise ValueError('Se requieren al menos 10 máximos anuales positivos, finitos y no constantes.')
    if not periods or any(not math.isfinite(t) or t<=1 for t in periods):
        raise ValueError('Los periodos de retorno deben ser finitos y mayores que 1 año.')
    models=[('Gumbel',stats.gumbel_r,False,2,{}),('GEV',stats.genextreme,False,3,{}),
            ('Lognormal',stats.lognorm,False,2,{'floc':0}),('Log-Pearson III',stats.pearson3,True,3,{})]
    rows=[]; quantiles=[]
    for name,distribution,logged,k,kwargs in models:
        try:
            sample=np.log(x) if logged else x
            params=distribution.fit(sample,**kwargs)
            # Jacobian makes log-scale and original-scale likelihoods comparable.
            logpdf=distribution.logpdf(sample,*params)-(np.log(x) if logged else 0)
            ll=float(np.sum(logpdf))
            q=distribution.ppf(1-1/np.asarray(periods),*params)
            if logged: q=np.exp(q)
            if not math.isfinite(ll) or not np.isfinite(q).all() or (q<0).any():
                raise ValueError('Ajuste o cuantiles no válidos')
            aic=2*k-2*ll; aicc=aic+2*k*(k+1)/(len(x)-k-1)
            cdf=distribution.cdf(np.sort(sample),*params)
            ks=float(max(np.max(np.arange(1,len(x)+1)/len(x)-cdf),np.max(cdf-np.arange(len(x))/len(x))))
            rows.append(dict(distribution=name,n=len(x),parameters=json.dumps([float(p) for p in params]),
                log_likelihood=ll,AIC=aic,AICc=aicc,BIC=k*math.log(len(x))-2*ll,KS_D=ks,status='OK'))
            quantiles.extend(dict(distribution=name,T_years=float(t),P24_mm=float(p)) for t,p in zip(periods,q))
        except Exception as exc:
            rows.append(dict(distribution=name,n=len(x),status=str(exc),AICc=None))
    valid=[row for row in rows if row['status']=='OK']
    if not valid: raise ValueError('Ninguna distribución produjo un ajuste válido.')
    best=min(valid,key=lambda row:row['AICc'])['distribution']
    for row in rows: row['best_AICc']=row['distribution']==best
    return rows,quantiles,best


def tc_all(area_km2,length_m,channel_slope,basin_slope_pct,mean_height_m,cn):
    args=(area_km2,length_m,channel_slope,basin_slope_pct,mean_height_m,cn)
    if not all(math.isfinite(v) and v>0 for v in args) or cn>100:
        raise ValueError('Área, longitud, pendientes y altura media sobre la salida deben ser positivos; CN debe estar entre 0 y 100.')
    L=length_m/1000
    methods={
        'Kirpich':0.01947*length_m**0.77*channel_slope**-0.385/60,
        'Giandotti':(4*math.sqrt(area_km2)+1.5*L)/(0.8*math.sqrt(mean_height_m)),
        'Témez':0.3*(L/channel_slope**0.25)**0.76,
        'NRCS/SCS lag':((length_m*3.28084)**0.8*(1000/cn-9)**0.7)/(1140*math.sqrt(basin_slope_pct))}
    methods['Promedio de métodos']=sum(methods.values())/len(methods)
    return methods


def runoff_mm(rain,cn):
    if not 0<cn<=100 or rain<0: raise ValueError('CN o lluvia fuera de rango.')
    retention=25400/cn-254
    return 0. if rain<=0.2*retention else (rain-0.2*retention)**2/(rain+0.8*retention)


def flow_comparison(designs,area,cn,times,duration,exponent,coefficient):
    if not 0<duration<=24 or not 0<coefficient<=1: raise ValueError('Duración o coeficiente C fuera de rango.')
    results=[]; hydrographs=[]
    for t,p24 in sorted(designs.items()):
        rain=p24*(duration/24)**exponent
        excess=runoff_mm(rain,cn)
        for method,tc in times.items():
            tp=duration/2+0.6*tc
            for model,runoff in [('SCS-CN / HU NRCS',excess),('HU triangular / coeficiente C',coefficient*rain)]:
                peak=0.208*area*runoff/tp; volume=runoff*area*1000
                # Choose base from volume conservation, not a separately rounded ratio.
                base=2*volume/(peak*3600) if peak else (8/3)*tp
                results.append(dict(T_years=t,Tc_method=method,runoff_method=model,Tc_h=tc,Tp_h=tp,
                    P24_mm=p24,Pduration_mm=rain,duration_h=duration,runoff_mm=runoff,volume_m3=volume,
                    Qpeak_m3s=peak,area_km2=area,CN_mean=cn,C=coefficient,status='OK'))
                for hour,q in ((0,0),(tp,peak),(base,0)):
                    hydrographs.append(dict(T_years=t,Tc_method=method,runoff_method=model,time_h=hour,Q_m3s=q))
            row=dict(T_years=t,Tc_method=method,runoff_method='Racional',Tc_h=tc,area_km2=area,C=coefficient,CN_mean=cn,P24_mm=p24)
            if 0<tc<=24:
                p_tc=p24*(tc/24)**exponent
                row.update(duration_h=tc,Pduration_mm=p_tc,intensity_mmh=p_tc/tc,Qpeak_m3s=(1/3.6)*coefficient*area*p_tc/tc,
                    status='Aproximación DDF; validar con IDF local y dominio del método racional')
            else: row.update(status='No calculado: Tc fuera del intervalo DDF (0,24] h')
            results.append(row)
    return results,hydrographs
