import warnings

import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from matplotlib.ticker import ScalarFormatter

from src.seba import EnergyBudget
from src.spectral_analysis import kappa_from_deg, kappa_from_lambda
from src.visualization import AnchoredText, fluxes_slices_by_models

params = {'xtick.labelsize': 'medium',
          'ytick.labelsize': 'medium',
          'text.usetex': True, 'font.size': 14,
          'font.family': 'serif', 'font.weight': 'normal'}
plt.rcParams.update(params)
plt.rcParams['legend.title_fontsize'] = 15

warnings.filterwarnings('ignore')

if __name__ == '__main__':

    # Load dyamond dataset
    model = 'ICON'
    resolution = 'n512'
    data_path = 'data/'
    # data_path = '/mnt/levante/energy_budget/test_data/'

    date_time = '20[0]'
    file_names = data_path + '{}_atm_3d_inst_{}_gps_{}.nc'

    # # load earth topography and surface pressure
    dataset_sfc = xr.open_dataset(data_path + 'ICON_sfcp_{}.nc'.format(resolution))
    sfc_pres = dataset_sfc.pres_sfc

    dataset_dyn = xr.open_mfdataset(file_names.format(model, resolution, date_time))

    # Create energy budget object
    budget = EnergyBudget(dataset_dyn, ps=sfc_pres, jobs=1)

    # Compute diagnostics
    dataset_energy = budget.energy_diagnostics()

    layers = {
        'Troposphere': [250e2, 500e2],
        'Stratosphere': [50e2, 250e2]
    }

    # ----------------------------------------------------------------------------------------------
    # Visualization of Kinetic energy and Available potential energy
    # ----------------------------------------------------------------------------------------------
    kappa = 1e3 * dataset_energy.kappa.values  # km^-1

    if kappa.size < 1000:
        x_limits = 1e3 * kappa_from_deg(np.array([0, 1000]))
        xticks = np.array([1, 10, 100, 1000])
    else:
        x_limits = 1e3 * kappa_from_deg(np.array([0, 2128]))
        xticks = np.array([2, 20, 200, 2000])

    y_limits = [1e-4, 5e7]

    x_lscale = kappa_from_lambda(np.linspace(3200, 650., 2))
    x_sscale = kappa_from_lambda(np.linspace(450, 60., 2))

    y_lscale = 5.0e-4 * x_lscale ** (-3.0)
    y_sscale = 0.20 * x_sscale ** (-5.0 / 3.0)

    x_lscale_pos = x_lscale.min()
    x_sscale_pos = x_sscale.min()

    y_lscale_pos = 2.6 * y_lscale.max()
    y_sscale_pos = 2.6 * y_sscale.max()

    s_lscale = r'$l^{-3}$'
    s_sscale = r'$l^{-5/3}$'

    fig, ax = plt.subplots(nrows=1, ncols=1, figsize=(7., 5.8), constrained_layout=True)

    ls = ['-', '--']
    for i, (layer, prange) in enumerate(layers.items()):
        data = dataset_energy.integrate_range(coord_range=prange).mean(dim='time')

        ax.loglog(kappa, data.hke, label=r'$E_K$', linewidth=1.2, linestyle=ls[i], color='red')
        ax.loglog(kappa, data.ape, label=r'$E_A$', linewidth=1.2, linestyle=ls[i], color='navy')
        ax.loglog(kappa, data.vke, label=r'$E_w$', linewidth=1.2, linestyle=ls[i], color='black')

    # Plot reference slopes
    ax.loglog(x_sscale, y_sscale, lw=1.2, ls='dashed', color='gray')
    ax.loglog(x_lscale, y_lscale, lw=1.2, ls='dashed', color='gray')

    ax.annotate(s_lscale,
                xy=(x_lscale_pos, y_lscale_pos), xycoords='data', color='gray',
                horizontalalignment='left', verticalalignment='top', fontsize=14)
    ax.annotate(s_sscale,
                xy=(x_sscale_pos, y_sscale_pos), xycoords='data', color='gray',
                horizontalalignment='left', verticalalignment='top', fontsize=14)

    at = AnchoredText(model.upper(), prop=dict(size=20), frameon=False, loc='upper left', )
    at.patch.set_boxstyle("round,pad=-0.3,rounding_size=0.2")
    ax.add_artist(at)

    ax.set_ylabel(r'Energy ($J~m^{-2}$)', fontsize=14)

    secax = ax.secondary_xaxis('top', functions=(kappa_from_lambda, kappa_from_lambda))

    ax.xaxis.set_major_formatter(ScalarFormatter())

    ax.set_xticks(1e3 * kappa_from_deg(xticks))
    ax.set_xticklabels(xticks)

    secax.xaxis.set_major_formatter(ScalarFormatter())

    ax.set_xlabel(r'Spherical harmonic degree', fontsize=14, labelpad=4)
    secax.set_xlabel(r'Spherical wavelength $(km)$', fontsize=14, labelpad=5)

    ax.set_xlim(*x_limits)
    ax.set_ylim(*y_limits)
    ax.legend(title=r"  Troposphere  /  Stratosphere", loc='upper right', fontsize=12, ncol=2)

    plt.show()

    # fig.savefig('figures/icon_total_energy_spectra_{}.pdf'.format(resolution), dpi=300)
    # plt.close(fig)

    # ----------------------------------------------------------------------------------------------
    # Nonlinear transfer of Kinetic energy and Available potential energy
    # ----------------------------------------------------------------------------------------------
    kappa = 1e3 * budget.kappa_h

    # get nonlinear energy fluxes. Compute time-averaged cumulative fluxes
    dataset_fluxes = budget.nonlinear_energy_fluxes().cumulative_sum(dim='kappa').mean(dim='time')

    # Perform vertical integration along last axis
    layers = {
        # 'Stratosphere': [50e2, 250e2],
        'Free troposphere': [250e2, 500e2]
    }
    limits = {
        'Stratosphere': [-0.4, 0.4],
        'Free troposphere': [-0.5, 1.0],
    }

    for i, (level, prange) in enumerate(layers.items()):
        data = dataset_fluxes.integrate_range(coord_range=prange)

        pik = data.pi_dke.values + data.pi_rke.values
        pia = data.pi_ape.values
        cka = data.cad.values
        lct = data.lc.values
        vfk = data.vf_dke.values
        vfa = data.vf_ape.values
        cdr = data.cdr.values

        # Create figure
        fig, ax = plt.subplots(nrows=1, ncols=1, figsize=(7.5, 5.8), constrained_layout=True)

        pit = pik + pia

        y_min = 1.5 * np.nanmin([pik, cdr])
        y_max = 1.5 * np.nanmax([pit, vfk + vfa, cka])

        y_limits = limits[level]

        at = AnchoredText(model.upper(), prop=dict(size=20), frameon=False, loc='upper left', )
        at.patch.set_boxstyle("round,pad=-0.3,rounding_size=0.2")
        ax.add_artist(at)

        ax.semilogx(kappa, pit, label=r'$\Pi = \Pi_K + \Pi_A$',
                    linewidth=2.5, linestyle='-', color='k')
        ax.semilogx(kappa, pik, label=r'$\Pi_K$', linewidth=1.6, linestyle='-', color='red')
        ax.semilogx(kappa, pia, label=r'$\Pi_A$', linewidth=1.6, linestyle='-', color='navy')

        ax.semilogx(kappa, cka, label=r'$C_{A\rightarrow D}$',
                    linewidth=1.6, linestyle='--', color='green')
        ax.semilogx(kappa, cdr, label=r'$C_{D\rightarrow R}$',
                    linewidth=1.6, linestyle='-.', color='cyan')

        # ax.semilogx(kappa, lct_l, label=r'$L_c$', linewidth=1.6, linestyle='--', color='orange')
        ax.semilogx(kappa, vfk + vfa, label=r'$F_{\uparrow}(p_b) - F_{\uparrow}(p_t)$',
                    linewidth=1.6, linestyle='-.', color='magenta')

        ax.set_ylabel(r'Cumulative energy flux ($W~m^{-2}$)', fontsize=15)

        ax.axhline(y=0.0, xmin=0, xmax=1, color='gray', linewidth=1.2, linestyle='dashed',
                   alpha=0.5)

        secax = ax.secondary_xaxis('top', functions=(kappa_from_lambda, kappa_from_lambda))
        secax.xaxis.set_major_formatter(ScalarFormatter())

        ax.xaxis.set_major_formatter(ScalarFormatter())
        ax.set_xticks(1e3 * kappa_from_deg(xticks))
        ax.set_xticklabels(xticks)

        ax.set_xlabel(r'Spherical harmonic degree', fontsize=14, labelpad=4)
        secax.set_xlabel(r'wavelength $(km)$', fontsize=14, labelpad=5)

        ax.set_xlim(*x_limits)
        ax.set_ylim(*y_limits)

        prange_str = [int(1e-2 * p) for p in sorted(prange)]

        ax.legend(title=r"{} ({:4d} - {:4d} hPa)".format(level, *prange_str),
                  loc='upper right', fontsize=14)
        plt.show()

    # ----------------------------------------------------------------------------------------------
    # Nonlinear transfer of Kinetic energy and Available potential energy
    # ----------------------------------------------------------------------------------------------
    kappa = 1e3 * budget.kappa_h

    # ----------------------------------------------------------------------------------------------
    # Load computed fluxes
    # ----------------------------------------------------------------------------------------------
    layers = {
        'Free troposphere': [250e2, 500e2],
        'Lower troposphere': [500e2, 950e2]
    }

    ke_limits = {'Free troposphere': [-0.6, 0.6],
                 'Lower troposphere': [-0.6, 0.6]}
    # perform vertical integration
    colors = ['green', 'magenta']
    if kappa.size < 1000:
        x_limits = 1e3 * kappa_from_deg(np.array([0, 1000]))
        xticks = np.array([1, 10, 100, 1000])
    else:
        x_limits = 1e3 * kappa_from_deg(np.array([0, 2048]))
        xticks = np.array([2, 20, 200, 2000])

    for i, (level, prange) in enumerate(layers.items()):
        # Integrate fluxes in layers
        data = dataset_fluxes.integrate_range(coord_range=prange)

        cad = data.cad.values
        pid = data.pi_dke.values
        pir = data.pi_rke.values

        cdr_w = data.cdr_w.values
        cdr_v = data.cdr_v.values
        cdr_c = data.cdr_c.values
        cdr = data.cdr.values - cdr_c

        # ------------------------------------------------------------------------------------------
        # Visualization of Kinetic energy budget
        # ------------------------------------------------------------------------------------------
        fig, ax = plt.subplots(nrows=1, ncols=1, figsize=(7.5, 5.8), constrained_layout=True)

        at = AnchoredText(model.upper(), prop=dict(size=20), frameon=False, loc='upper left', )
        at.patch.set_boxstyle("round,pad=-0.3,rounding_size=0.2")
        ax.add_artist(at)

        # ax.semilogx(kappa, cad, label=r'$C_{A\rightarrow D}$',
        #             linewidth=1.6, linestyle='-', color='green')

        ax.semilogx(kappa, pid + pir, label=r'$\Pi_K$', linewidth=2., linestyle='-', color='k')

        ax.semilogx(kappa, pid, label=r'$\Pi_D$', linewidth=1.6, linestyle='-', color='green')
        ax.semilogx(kappa, pir, label=r'$\Pi_R$', linewidth=1.6, linestyle='-', color='red')

        ax.semilogx(kappa, cdr, label=r'$C_{D \rightarrow R}$',
                    linewidth=2., linestyle='-', color='blue')

        ax.semilogx(kappa, cdr_w, label=r'Vertical motion', linewidth=1.6,
                    linestyle='-.', color='black')

        ax.semilogx(kappa, cdr_v, label=r'Relative vorticity', linewidth=1.6,
                    linestyle='-.', color='red')

        # ax.semilogx(kappa, cdr_cl, label=r'Coriolis', linewidth=1.6,
        #             linestyle='-.', color='green')

        ax.set_ylabel(r'Cumulative energy flux ($W~m^{-2}$)', fontsize=16)

        ax.axhline(y=0.0, xmin=0, xmax=1, color='gray', linewidth=1., linestyle='dashed', alpha=0.5)

        secax = ax.secondary_xaxis('top', functions=(kappa_from_lambda, kappa_from_lambda))
        secax.xaxis.set_major_formatter(ScalarFormatter())

        ax.xaxis.set_major_formatter(ScalarFormatter())

        ax.set_xticks(1e3 * kappa_from_deg(xticks))
        ax.set_xticklabels(xticks)

        ax.set_xlabel(r'wavenumber', fontsize=16, labelpad=4)
        secax.set_xlabel(r'wavelength $(km)$', fontsize=16, labelpad=5)

        ax.set_xlim(*x_limits)
        ax.set_ylim(*ke_limits[level])

        prange_str = [int(1e-2 * p) for p in sorted(prange)]

        legend = ax.legend(title=r"{} ({:4d} - {:4d} hPa)".format(level, *prange_str),
                           loc='upper right', fontsize=14, ncol=2)
        ax.add_artist(legend)

        plt.show()
        plt.close(fig)

    # ---------------------------------------------------------------------------------------
    # Visualize fluxes cross section
    # ---------------------------------------------------------------------------------------
    figure_name = './figures/{}_fluxes_section_{}.pdf'.format(model, resolution)

    fluxes_slices_by_models(dataset_fluxes, model=None, variables=['cdr', 'vf_dke'],
                            resolution='n1024', y_limits=[1000., 100.],
                            fig_name=figure_name)