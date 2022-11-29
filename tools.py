import functools
import multiprocessing as mp

import _spherepack
import numpy as np
import scipy.signal as sig
import scipy.special as spec
from scipy.spatial import cKDTree

from spectral_analysis import lambda_from_deg


def _find_coordinates(array, predicate, name):
    """
    Find a dimension coordinate in an `xarray.DataArray` that satisfies
    a predicate function.
    """
    candidates = [coord
                  for coord in [array.coords[n] for n in array.dims]
                  if predicate(coord)]
    if not candidates:
        raise ValueError('cannot find a {!s} coordinate'.format(name))
    if len(candidates) > 1:
        msg = 'multiple {!s} coordinates are not allowed'
        raise ValueError(msg.format(name))
    coord = candidates[0]
    dim = array.dims.index(coord.name)
    return coord, dim


def _find_latitude_coordinate(array):
    """Find a latitude dimension coordinate in an `xarray.DataArray`."""
    return _find_coordinates(
        array,
        lambda c: (c.name in ('latitude', 'lat') or
                   c.attrs.get('units') == 'degrees_north' or
                   c.attrs.get('axis') == 'Y'), 'latitude')


def _find_variable(dataset, var_info):
    """
    Find a dimension coordinate in an `xarray.DataArray` that satisfies
    a predicate function.
    """
    # try by name selection
    array = dataset.variables.get(var_info['name'])

    if array is None:
        # try flexible candidates for the variable based on 'info_dict'
        def predicate(d):
            return (var_info['name'] == d.name or
                    var_info['units'] == d.attrs.get('units').lower() or
                    d.attrs.get('axis') == 'TZ--')

        # look for candidates
        candidates = [dataset.variables.get(name)
                      for name, values in dataset.variables.items()
                      if predicate(values)]

        if not candidates:
            raise ValueError('cannot find a variable {!s}'.format(var_info['name']))

        array = candidates[0].values

    return array


# def parse_dataset(dataset, variables=None):
#
#     if variables is None:
#         variables = ['u', 'v', 'w', 't', 'p']
#
#     # Get coordinates and dimensions
#     coords = {name: dataset.coords[name].axis for name in dataset.dims}
#
#     dims_size = dict(dataset.dims)
#
#     # string needed for data preparation
#     info_coords = ''.join(coords.values()).lower()
#
#     # get coordinates
#     for var in dataset:
#        coords = {name: var.coords[name].axis for name in var.dims}
#
#     return


def prepare_data(data, dim_order):
    """
    Prepare data for input to `EnergyBudget` method calls.

    Parameters:
    -----------
        data: `ndarray`
          Data array. The array must be at least 3D.
        dim_order: `string`,
          String specifying the order of dimensions in the data array. The
          characters 'x' and 'y' represent longitude and latitude
          respectively. Any other characters can be used to represent
          other dimensions.
    Returns:
    --------
        pdata: `ndarray`
          data reshaped/reordered to (latitude, longitude, other, levels).

        info_dict: `dict`
            A dictionary of information required to recover data.

    Examples:
    _________
    Prepare an array with dimensions (12, 17, 73, 144, 2) where the
    dimensions are (time, level, latitude, longitude, other):
      pdata, out_order = prep_data(data, 'tzyxs')

    The ordering of the output data dimensions is out_order = 'yx(ts)z',
    where the non-spatial dimensions between brackets are packed into a single axis:
    pdata.shape = (73, 144, 24, 17)
    """
    if data.ndim < 3:
        raise ValueError('Input fields must be at least 3D')

    if len(dim_order) > data.ndim:
        raise ValueError("Inconsistent number dimensions"
                         "'dim_order' must have length {}".format(data.ndim))

    if 'x' not in dim_order or 'y' not in dim_order:
        raise ValueError('A latitude-longitude grid is required')

    if 'z' not in dim_order:
        raise ValueError('A vertical grid is required')

    spatial_dims = [dim_order.lower().find(dim) for dim in 'yxz']

    data = np.moveaxis(data, spatial_dims, [0, 1, -1])

    # pack sample dimension
    inter_shape = data.shape

    data = data.reshape(inter_shape[:2] + (-1, inter_shape[-1]))  # .squeeze()

    out_order = dim_order.replace('x', '')
    out_order = out_order.replace('y', '')
    out_order = out_order.replace('z', '')
    out_order = 'yx(' + out_order + ')z'

    info_dict = {
        'interm_shape': inter_shape,
        'origin_order': dim_order,
        'output_order': out_order,
    }
    return data, info_dict


def recover_data(data, info_dict):
    """
    Recover the shape and dimension order of an array output
    after calling 'prepare_data'.
    """
    data = data.reshape(info_dict['interm_shape'])

    spatial_dims = [info_dict['origin_order'].find(dim) for dim in 'yxz']

    return np.moveaxis(data, [0, 1, -1], spatial_dims)


def get_chunk_size(n_workers, len_iterable, factor=4):
    """Calculate chunk size argument for Pool-methods.

    Resembles source-code within `multiprocessing.pool.Pool._map_async`.
    """
    chunk_size, extra = divmod(len_iterable, n_workers * factor)
    if extra:
        chunk_size += 1
    return chunk_size


def number_chunks(sample_size, workers):
    # finds the integer factor of 'sample_size' closest to 'workers'
    # for parallel computations: ensures maximum cpu usage for chunk_size = 1
    jobs = workers
    while sample_size % jobs:
        jobs -= 1
    return jobs if jobs != 1 else workers


def getspecindx(ntrunc):
    """
     compute indices of zonal wavenumber (index_m) and degree (index_n)
     for complex spherical harmonic coefficients.
     @param ntrunc: spherical harmonic triangular truncation limit.
     @return: C{B{index_m, index_n}} - rank 1 numpy Int32 arrays
     containing zonal wavenumber (index_m) and degree (index_n) of
     spherical harmonic coefficients.
    """
    index_m, index_n = np.indices((ntrunc + 1, ntrunc + 1))

    indices = np.nonzero(np.greater(index_n, index_m - 1).flatten())
    index_n = np.take(index_n.flatten(), indices)
    index_m = np.take(index_m.flatten(), indices)

    return np.squeeze(index_m), np.squeeze(index_n)


def transform_io(func, order='C'):
    """
    Decorator for handling arrays' IO dimensions for calling spharm's spectral functions.
    The dimensions of the input arrays with shapes (nlat, nlon, nlev, ntime, ...) or (ncoeffs, nlev, ntime, ...)
    are packed to (nlat, nlon, samples) and (ncoeffs, samples) respectively, where ncoeffs = (ntrunc+1)*(ntrunc+2)/2.
    Finally, the outputs are transformed back to the original shape where needed.

    Parameters:
    -----------
    func: decorated function
    order: {‘C’, ‘F’, ‘A’}, optional
        Reshape the elements of the input arrays using this index order.
        ‘C’ means to read / write the elements using C-like index order, with the last axis index changing fastest,
        back to the first axis index changing slowest. See 'numpy.reshape' for details.
    """

    @functools.wraps(func)
    def dimension_packer(*args, **kwargs):
        # self passed as first argument
        self, *_ = args
        transformed_args = [self, ]
        for arg in args:
            if isinstance(arg, np.ndarray):
                transformed_args.append(self._pack_levels(arg, order=order))

        results = func(*transformed_args, **kwargs)
        # convert output back to original shape
        return self._unpack_levels(results, order=order)

    return dimension_packer


def regular_lats_wts(nlat):
    """
        Computes the latitude points and weights of a regular grid
        (equally spaced in longitude and latitude). Regular grids
        will include the poles and equator if nlat is odd. The sampling
        is a constant 180 deg/nlat. Weights are defined as the cosine of latitudes.
    """
    ns_latitude = 90. - (nlat + 1) % 2 * (90. / nlat)

    lats = np.linspace(ns_latitude, -ns_latitude, nlat)

    return lats, np.cos(np.deg2rad(lats))


def gaussian_lats_wts(nlat):
    """
     compute the gaussian latitudes (in degrees) and quadrature weights.
     @param nlat: number of gaussian latitudes desired.
     @return: C{B{lats, wts}} - rank 1 numpy float64 arrays containing
     gaussian latitudes (in degrees north) and gaussian quadrature weights.
    """

    # get the gaussian co-latitudes and weights using gaqd.
    colats, wts, ierror = _spherepack.gaqd(nlat)

    if ierror:
        raise ValueError('In return from call to _spherepack.gaqd'
                         'ierror =  {:d}'.format(ierror))

    # convert co-latitude to degrees north latitude.
    lats = 90.0 - colats * 180.0 / np.pi
    return lats, wts


def latitudes_weights(nlat, gridtype):
    # Calculate latitudes and weights based on gridtype
    if gridtype == 'gaussian':
        # Get latitude of the gaussian grid and quadrature weights
        lats, weights = gaussian_lats_wts(nlat)
    else:
        # Get latitude of the regular grid and quadrature weights
        lats, weights = regular_lats_wts(nlat)
    return lats, weights


def infer_gridtype(latitudes):
    """
    Determine a grid type by examining the points of a latitude
    dimension.
    Raises a ValueError if the grid type cannot be determined.
    **Argument:**
    *latitudes*
        An iterable of latitude point values.
    **Returns:**
    *gridtype*
        Either 'gaussian' for a Gaussian grid or 'regular' for an
        equally-spaced grid.
    *reference latitudes*
    *quadrature weights*
    """
    # Define a tolerance value for differences, this value must be much
    # smaller than expected grid spacings.
    tolerance = 5e-8

    # Get the number of latitude points in the dimension.
    nlat = len(latitudes)
    diffs = np.abs(np.diff(latitudes))
    equally_spaced = (np.abs(diffs - diffs[0]) < tolerance).all()

    if equally_spaced:
        # The latitudes are equally-spaced. Construct reference global
        # equally spaced latitudes and check that the two match.
        reference, wts = regular_lats_wts(nlat)

        if not np.allclose(latitudes, reference, atol=tolerance):
            raise ValueError('Invalid equally-spaced latitudes (they may be non-global)')
        gridtype = 'regular'
    else:
        # The latitudes are not equally-spaced, which suggests they might
        # be gaussian. Construct sample gaussian latitudes and check if
        # the two match.
        reference, wts = gaussian_lats_wts(nlat)

        if not np.allclose(latitudes, reference, atol=tolerance):
            raise ValueError('latitudes are neither equally-spaced or Gaussian')
        gridtype = 'gaussian'

    return gridtype, reference, wts


def cumulative_flux(spectra):
    """
    Computes cumulative spectral energy transfer. The spectra are added starting
    from the largest wave number N (triangular truncation) to a given degree l.
    """
    spectral_flux = np.zeros_like(spectra)

    # Set fluxes to 0 at ls=0 to avoid small truncation errors.
    for ln in range(2, spectra.shape[0]):
        spectral_flux[ln] = spectra[ln:].sum(axis=0)

    return spectral_flux


def kernel_2d(fc, n):
    """ Generate a low-pass Lanczos kernel
        :param fc: float or  iterable [float, float],
            cutoff frequencies for each dimension (normalized by the sampling frequency)
        :param n: size of one quadrant of the circular kernel.
    """
    fc_sq = np.prod(fc)
    ns = 2 * n + 1

    # construct wavenumbers
    k = np.moveaxis(np.indices([ns, ns]) - n, 0, -1)

    z = np.sqrt(np.sum((fc * k) ** 2, axis=-1))
    w = fc_sq * spec.j1(2 * np.pi * z) / z.clip(1e-12)
    w *= np.prod(spec.sinc(np.pi * k / n), axis=-1)

    w[n, n] = np.pi * fc_sq

    return w / w.sum()


def convolve_chunk(a, func):
    return np.array([func(ai) for ai in a])


def lowpass_lanczos(data, window_size, cutoff_freq, axis=None, jobs=None):
    if axis is None:
        axis = -1

    arr = np.moveaxis(data, axis, 0)

    if jobs is None:
        jobs = min(mp.cpu_count(), arr.shape[0])

    # compute lanczos 2D kernel for convolution
    kernel = kernel_2d(cutoff_freq, window_size)
    kernel = np.expand_dims(kernel, 0)

    # wrapper of convolution function for parallel computations
    # convolve_2d = functools.partial(sig.convolve2d, in2=kernel, boundary='symm', mode='same')
    convolve_2d = functools.partial(sig.fftconvolve, in2=kernel, mode='same', axes=(1, 2))

    # Chunks of arrays along axis=0 for the mp mapping ...
    chunks = np.array_split(arr, jobs, axis=0)

    # Create pool of workers
    pool = mp.Pool(processes=jobs)

    # Applying 2D lanczos filter to data chunks
    # result = pool.map(functools.partial(convolve_chunk, func=convolve2d), chunks)
    result = pool.map(convolve_2d, chunks)

    # Freeing the workers:
    pool.close()
    pool.join()

    result = np.concatenate(result, axis=0)
    result[np.isnan(result)] = 1.0

    return np.moveaxis(result, 0, axis)


def intersections(coords, a, b, direction='all'):
    #
    index_coords, _ = find_intersections(coords, a, b, direction=direction)

    if len(index_coords) == 0:
        # print('No intersections found in data')
        return np.nan
    else:
        return index_coords


def find_intersections(x, a, b, direction='all'):
    """Calculate the best estimate of intersection.

    Calculates the best estimates of the intersection of two y-value
    data sets that share a common x-value set.

    Parameters
    ----------
    x : array-like
        1-dimensional array of numeric x-values
    a : array-like
        1-dimensional array of y-values for line 1
    b : array-like
        1-dimensional array of y-values for line 2
    direction : string
        specifies direction of crossing. 'all', 'increasing' (a becoming greater than b),
        or 'decreasing' (b becoming greater than a).

    Returns
    -------
        A tuple (x, y) of array-like with the x and y coordinates of the
        intersections of the lines.
    """
    # Find the index of the points just before the intersection(s)
    nearest_idx = nearest_intersection_idx(a, b)
    next_idx = nearest_idx + 1

    # Determine the sign of the change
    sign_change = np.sign(a[next_idx] - b[next_idx])

    # x-values around each intersection
    _, x0 = _next_non_masked_element(x, nearest_idx)
    _, x1 = _next_non_masked_element(x, next_idx)

    # y-values around each intersection for the first line
    _, a0 = _next_non_masked_element(a, nearest_idx)
    _, a1 = _next_non_masked_element(a, next_idx)

    # y-values around each intersection for the second line
    _, b0 = _next_non_masked_element(b, nearest_idx)
    _, b1 = _next_non_masked_element(b, next_idx)

    # Calculate the x-intersection.
    delta_y0 = a0 - b0
    delta_y1 = a1 - b1
    intersect_x = (delta_y1 * x0 - delta_y0 * x1) / (delta_y1 - delta_y0)

    # Calculate the y-intersection of the lines.
    intersect_y = ((intersect_x - x0) / (x1 - x0)) * (a1 - a0) + a0

    # Make a mask based on the direction of sign change desired
    if direction == 'increasing':
        mask = sign_change > 0
    elif direction == 'decreasing':
        mask = sign_change < 0
    elif direction == 'all':
        return intersect_x, intersect_y
    else:
        raise ValueError(
            'Unknown option for direction: {0}'.format(str(direction)))
    return intersect_x[mask], intersect_y[mask]


def nearest_intersection_idx(a, b):
    """Determine the index of the point just before two lines with common x values.

    Parameters
    ----------
    a : array-like
        1-dimensional array of y-values for line 1
    b : array-like
        1-dimensional array of y-values for line 2

    Returns
    -------
        An array of indexes representing the index of the values
        just before the intersection(s) of the two lines.
    """
    # Determine the points just before the intersection of the lines
    sign_change_idx, = np.nonzero(np.diff(np.sign(a - b)))

    return sign_change_idx


def _next_non_masked_element(x, idx):
    """Return the next non-masked element of a masked array.

    If an array is masked, return the next non-masked element (if the given index is masked).
    If no other unmasked points are after the given masked point, returns none.

    Parameters
    ----------
    x : array-like
        1-dimensional array of numeric values
    idx : integer
        index of requested element

    Returns
    -------
        Index of next non-masked element and next non-masked element
    """
    try:
        next_idx = idx + x[idx:].mask.argmin()
        if np.ma.is_masked(x[next_idx]):
            return None, None
        else:
            return next_idx, x[next_idx]
    except (AttributeError, TypeError, IndexError):
        return idx, x[idx]


def search_closet(points, target_points):
    if target_points is None:
        return slice(None)
    else:
        points = np.atleast_2d(points).T
        target_points = np.atleast_2d(target_points).T
        # creates a search tree
        # noinspection PyArgumentList
        search_tree = cKDTree(points)
        # nearest neighbour (k=1) in levels to each point in target levels
        _, nn_idx = search_tree.query(target_points, k=1)

        return nn_idx


def terrain_mask(p, ps, smooth=True, jobs=None):
    """
    Creates a terrain mask based on surface pressure and pressure profile
    :param: smoothed, optional
        Apply a low-pass filter to the terrain mask
    :return: 'np.array'
        beta contains 0 for levels satisfying p > ps and 1 otherwise
    """

    nlevels = p.size
    nlat, nlon = ps.shape

    # Search last level pierced by terrain for each vertical column
    level_m = p.size - np.searchsorted(np.sort(p), ps)
    # level_m = search_closet(p, ps)

    # create mask
    beta = np.zeros((nlat, nlon, nlevels))

    for ij in np.ndindex(*level_m.shape):
        beta[ij][level_m[ij]:] = 1.0

    if smooth:  # generate a smoothed heavy-side function
        # Calculate normalised cut-off frequencies for zonal and meridional directions:
        resolution = lambda_from_deg(nlon)  # grid spacing at the Equator
        cutoff_scale = lambda_from_deg(80)  # wavenumber 40 (scale ~500 km) from A&L (2013)

        # Normalized spatial cut-off frequency (cutoff_frequency / sampling_frequency)
        cutoff_freq = resolution / cutoff_scale
        window_size = (2.0 / np.min(cutoff_freq)).astype(int)  # window size set to cutoff scale

        # Apply low-pass Lanczos filter for smoothing:
        beta = lowpass_lanczos(beta, window_size, cutoff_freq, axis=-1, jobs=jobs)

    return beta.clip(0.0, 1.0)
