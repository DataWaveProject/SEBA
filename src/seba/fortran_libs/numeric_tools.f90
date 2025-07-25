!===================================================================================================
    subroutine model_error(message)

        implicit none

        character (len = *), intent (in) :: message

        call model_message('Execution aborted with status', 'Traceback:', 1.d0, 'I2')
        call model_message(message, '.', 0.d0, 'I2')
        stop

        return
    end subroutine model_error

    subroutine model_message(main_message, optn_message, values, vfmt)

        implicit none

        character (len = *), intent(in) :: main_message
        character (len = *), intent(in) :: optn_message
        character (len = *), intent(in) :: vfmt

        double precision, intent(in) :: values
        character (len = 500) :: str_fmt
        integer :: ios

        str_fmt = '("' // trim(adjustl(main_message)) // '",' // trim(vfmt) // ',"' // &
                trim(adjustl(optn_message)) // '")'

        if (vfmt(1:1) == 'I') then
            write(unit = *, fmt = trim(adjustl(str_fmt)), iostat = ios) int(values)
        else
            write(unit = *, fmt = trim(adjustl(str_fmt)), iostat = ios) values
        endif

        if (ios /= 0) stop 'write error in unit '

        return
    end subroutine model_message
    !===================================================================================================

    !===================================================================================================
    integer function truncation(nspc)

        ! This function computes the triangular truncation given the number of spectral coefficients
        implicit none
        integer, intent(in) :: nspc

        ! compute triangular truncation
        truncation = int(-1.5 + 0.5 * sqrt(9. - 8. * (1. - float(nspc)))) + 1

        return
    end function truncation
    !===================================================================================================

    !===================================================================================================
    subroutine getspecindx(index_mn, ntrunc)
        ! This subroutine returns the spectral indices corresponding
        ! to the spherical harmonic degree l and the order m.

        implicit none

        integer, intent(in) :: ntrunc
        integer, intent(out) :: index_mn(2, (ntrunc + 1) * (ntrunc + 2) / 2)

        ! local
        integer :: m, n, nmstrt, nm

        ! create spectral indices
        nmstrt = 0

        !$OMP PARALLEL DO DEFAULT(SHARED) PRIVATE(m)
        do m = 1, ntrunc + 1

            do n = m, ntrunc + 1
                nm = nmstrt + n - m + 1
                index_mn(:, nm) = [m, n]
            enddo
            nmstrt = nmstrt + ntrunc - m + 2
        enddo

        return
    end subroutine getspecindx
    !===================================================================================================

    !===================================================================================================
    subroutine onedtotwod(spec_2d, spec_1d, nlat, nspc, nt)
        implicit none
        integer, intent(in) :: nlat, nspc, nt
        double complex, intent(in) :: spec_1d(nspc, nt)
        double complex, intent(out) :: spec_2d(nlat, nlat, nt)
        integer :: ntrunc, truncation
        integer :: n, m, mn

        integer :: m_start(nlat)

        ! Compute truncation
        ntrunc = truncation(nspc)

        ! Precompute nmstrt offsets safely
        m_start(1) = 0
        do m = 2, nlat
            m_start(m) = m_start(m - 1) + ntrunc - (m - 1) + 1
        enddo

        spec_2d = 0.0

        !$OMP PARALLEL DO DEFAULT(SHARED) PRIVATE(m, n, mn)
        do m = 1, nlat
            do n = m, nlat
                mn = m_start(m) + n - m + 1
                if (mn <= nspc) then
                    spec_2d(m, n, :) = spec_1d(mn, :)
                endif
            enddo
        enddo
    end subroutine onedtotwod
    !===================================================================================================

    !===================================================================================================
    subroutine twodtooned(spec_1d, spec_2d, nlat, ntrunc, nt)
        implicit none

        ! input parameters
        integer, intent(in) :: nlat, ntrunc, nt
        double complex, intent(in) :: spec_2d(nlat, nlat, nt)

        ! output array
        double complex, intent(out) :: spec_1d(ntrunc * (ntrunc + 1) / 2, nt)

        ! local variables
        integer :: m, n, nm
        integer :: m_start(ntrunc)

        ! check number of coefficients
        if (ntrunc > nlat) then
            call model_error("Bad number of coefficients: truncation > nlat - 1")
        end if

        ! initialize output
        spec_1d = (0.0d0, 0.0d0)

        ! precompute nmstrt offsets
        m_start(1) = 0
        do m = 2, ntrunc
            m_start(m) = m_start(m - 1) + ntrunc - (m - 1) + 1
        enddo

        !$OMP PARALLEL DO DEFAULT(SHARED) PRIVATE(m, n, nm)
        do m = 1, ntrunc
            do n = m, ntrunc
                nm = m_start(m) + n - m + 1
                spec_1d(nm, :) = spec_2d(m, n, :)
            enddo
        enddo

    end subroutine twodtooned
    !===================================================================================================

    !===================================================================================================
    subroutine cumulative_spectrum(spectrum, cs_lm, ntrunc, nspc, ns, flux_form)
        ! input-output parameters
        implicit none

        integer, intent(in) :: ntrunc, nspc, ns
        double complex, intent(in) :: cs_lm     (nspc, ns)
        double precision, intent(out) :: spectrum  (ntrunc, ns)
        logical, intent(in) :: flux_form

        ! lcal variables
        double precision :: tmp(ns)
        double complex :: scaled_cs (ntrunc, ntrunc, ns)
        integer :: ln

        ! Reshape the spectral coefficients to matrix form (2, ntrunc, ntrunc, ...)
        call onedtotwod(scaled_cs, cs_lm, ntrunc, nspc, ns)

        ! Scale non-symmetric coefficients (ms != 1) by two
        scaled_cs(2:ntrunc, :, :) = 2.0 * scaled_cs(2:ntrunc, :, :)

        ! Initialize array for the 1D energy/power spectrum shaped (truncation, ...)
        spectrum = 0.0

        ! Compute spectrum as a function of total wavenumber: SUM Cml(m <= l).
        !$OMP PARALLEL DO DEFAULT(SHARED) PRIVATE(ln, tmp)
        do ln = 1, ntrunc
            tmp = real(sum(scaled_cs(1:ln, ln, :), dim = 1))
            spectrum(ln, :) = tmp
        end do

        ! Convert to spectral flux by accumulating for all wavenumbers n >= l for each degree l.
        if (flux_form) then

            do ln = 1, ntrunc
                spectrum(ln, :) = sum(spectrum(ln:ntrunc, :), dim = 1)
            enddo
        end if

        return
    end subroutine cumulative_spectrum
    !===================================================================================================

    !===================================================================================================
    subroutine cross_spectrum(spectrum, clm_1, clm_2, ntrunc, nspc, ns)

        implicit none

        ! input-output parameters
        integer, intent(in) :: ntrunc, nspc, ns

        double complex, intent(in) :: clm_1(nspc, ns)
        double complex, intent(in) :: clm_2(nspc, ns)
        double precision, intent(out) :: spectrum(ntrunc, ns)

        ! lcal variables
        double complex :: clm_cs   (nspc, ns)
        logical :: flux_form

        flux_form = .false.

        ! Compute cross spectrum in (m, l) space
        clm_cs = clm_1 * conjg(clm_2)

        ! Compute spectrum as a function of total wavenumber: SUM Cml(m <= l).
        call cumulative_spectrum(spectrum, clm_cs, ntrunc, nspc, ns, flux_form)

        return
    end subroutine cross_spectrum
    !===================================================================================================

    !===================================================================================================
    subroutine surface_temperature(sfct, sfcp, temp, pres, nt, ns, np)

        implicit none

        integer, intent(in) :: nt, ns, np
        double precision, intent(in) :: pres(np)
        double precision, intent(in) :: sfcp(ns)
        double precision, intent(in) :: temp(nt, ns, np)

        double precision, intent(out) :: sfct(nt, ns)

        integer :: i, j, ks, ke
        double precision :: safe_temp, increment
        logical :: mask(np)

        sfct = 0.0
        safe_temp = 120.0d0

        !$OMP PARALLEL DO COLLAPSE(2) DEFAULT(SHARED) PRIVATE(i, j, mask, ks, ke, increment)
        do j = 1, ns
            do i = 1, nt
                ! Compute mask: valid temperature values only
                mask = pack(temp(i, j, :) > safe_temp, .not.isnan(temp(i, j, :)))

                ! Find valid index closest to surface pressure [pres(ke) <= sfcp(j)]
                ks = minloc(abs(pres - sfcp(j)), mask = mask, dim = 1)
                ke = min(ks + 1, np)

                if (ks == ke) then
                    sfct(i, j) = temp(i, j, ks)
                else
                    increment = (sfcp(j) - pres(ks)) / (pres(ke) - pres(ks))
                    sfct(i, j) = temp(i, j, ks) + (temp(i, j, ke) - temp(i, j, ks)) * increment
                end if
            end do
        end do

    end subroutine surface_temperature
    !===================================================================================================

    !===================================================================================================
    subroutine geopotential(phi, pres, temp, sfch, sfcp, sfct, nt, ns, np)

        implicit none

        integer, intent(in) :: nt, ns, np

        double precision, intent(in) :: pres(np)
        double precision, intent(in) :: sfch(ns)
        double precision, intent(in) :: sfcp(ns)
        double precision, optional, intent(in) :: sfct(nt, ns)
        double precision, intent(in) :: temp(nt, ns, np)

        double precision, intent(out) :: phi(nt, ns, np)

        double precision :: sfc_temp(nt, ns)
        double precision :: lnp(np)
        double precision :: lnps(ns)

        double precision :: Rd, g
        double precision :: phi_bc, tbar
        integer :: i, j, nc, kn

        Rd = 287.058 ! gas constant for dry air (J / kg / K)
        g = 9.806650 ! acceleration of gravity  (m / s**2)

        ! initializing geopotential array
        phi = 0.0

        ! local arrays for working on log-pressure coordinates
        lnp = log(pres)
        lnps = log(sfcp)

        ! Determine surface temperature field
        if (present(sfct)) then
            sfc_temp = sfct
        else
            ! Approximate surface temperature by linear extrapolation
            call surface_temperature(sfc_temp, lnps, temp, lnp, nt, ns, np)
        end if

        ! Parallel vertical integration of hydrostatic equation
        !$OMP PARALLEL DO COLLAPSE(2) DEFAULT(SHARED) PRIVATE(i, j, kn, nc, phi_bc, tbar)
        do j = 1, ns
            do i = 1, nt
                ! find the first level above the surface (p <= sfcp)
                kn = minloc(abs(pres - sfcp(j)), mask = pres <= sfcp(j), dim = 1)
                nc = np + 1 - kn

                ! Using second order accurate mid-point method in log-pressure for the
                ! first integration step. Average temperature in the first two levels
                tbar = (lnp(kn) * temp(i, j, kn) + lnps(j) * sfc_temp(i, j)) / (lnp(kn) + lnps(j))
                phi_bc = g * sfch(j) - Rd * (lnp(kn) - lnps(j)) * tbar

                ! Vertical integration for levels above the surface: d(phi)/d(log p) = - Rd * T(p)
                ! Integrating in log-pressure using a 4th-order Adams-Moulton linear multistep method
                call adaptative_adams_moulton(phi(i, j, kn:np), phi_bc, &
                        &                         -Rd * temp(i, j, kn:np), lnp(kn:np), nc)

                ! Vertical integration for levels below the surface starting at p = ps.
                if (kn > 1) then
                    call adaptative_adams_moulton(phi(i, j, kn:1:-1), phi(i, j, kn), &
                            &             -Rd * temp(i, j, kn:1:-1), lnp(kn:1:-1), kn)
                end if
            end do
        end do

    end subroutine geopotential
    !===================================================================================================

    !===================================================================================================
    subroutine gradient(dpds, var, ds, ns, nt, order, mask)

        implicit none

        ! input vars ...
        integer, intent(in) :: order
        integer, intent(in) :: ns, nt
        double precision, intent(in) :: ds
        double precision, intent(in) :: var (ns, nt)
        integer, optional, intent(in) :: mask (nt)

        ! local
        integer :: k, m
        integer :: start_index (nt)

        ! output vars ...
        double precision, intent(out) :: dpds (ns, nt)

        dpds = 0.0

        if (present(mask)) then
            start_index = mask
        else
            start_index = 1
        end if

        ! loop over samples and compute gradient for each column

        !$OMP PARALLEL DO DEFAULT(SHARED) PRIVATE(k)
        do k = 1, nt
            ! m determines the first level above which to compute gradient
            m = start_index(k)
            call compact_derivative(dpds(m:ns, k), var(m:ns, k), ds, ns + 1 - m, order)
        enddo

        return
    end subroutine gradient
    !===================================================================================================

    !===================================================================================================
    subroutine linederv(ads, var, ds, ns, nso, nsf, order)

        ! module for computing high order centered finite differences formulas for
        ! aproximating first derivatives amoung a line.
        !
        !                       !  ds  !
        !
        ! *------*------ ... ---*------*------*------*------*--- ... ------*------*
        ! 1     nso            j-2    j-1     j     j+1    j+2            nsf     ns
        !
        ! options: ord = 1-2, 3-4, 5-6
        !
        ! uses:
        !
        ! advection (x) = u * linederv( u(x) , dx, nx, nxo,nxf, 6 )
        !
        implicit none

        ! inputs :
        integer, intent(in) :: order
        integer, intent(in) :: ns, nso, nsf
        double precision, intent(in) :: ds
        double precision, intent(in) :: var (ns)

        ! local :
        integer :: i
        double precision :: cflux2, cflux4
        double precision :: bflux2

        ! output :
        double precision, intent(out) :: ads (ns)

        ads = 0.0

        ! All stencils are centered differences formulas (2nd, 4th and 6th order)

        ! compute fluxes ...
        select case(order)

        case(2)

            do i = nso, nsf
                ads(i) = cflux2(var(i - 1), var(i + 1))
            enddo

            ! foward/backward schemes O( ds ^ 2 )
            ads(nso - 1) = bflux2(var(nso - 1), var(nso), var(nso + 1))
            ads(nsf + 1) = - bflux2(var(nsf + 1), var(nsf), var(nsf - 1))

        case(4)

            do i = nso + 1, nsf - 1
                ads(i) = cflux4(var(i - 2), var(i - 1), var(i + 1), var(i + 2))
            enddo

            ads(nso) = cflux2(var(nso - 1), var(nso + 1))
            ads(nsf) = cflux2(var(nsf - 1), var(nsf + 1))

            ads(nso - 1) = bflux2(var(nso - 1), var(nso), var(nso + 1))
            ads(nsf + 1) = - bflux2(var(nsf + 1), var(nsf), var(nsf - 1))

        case default

            call model_error('wrong flux operator: order = 2 or 4')

        end select

        ads = ads / ds

        return
    end subroutine linederv
    !===================================================================================================

    !===================================================================================================
    subroutine compact_derivative(ads, var, ds, ns, order)

        !
        ! module for computing high order centered compact finite differences
        ! formulas for aproximating first derivatives amoung a line. The tridiagonal
        ! system of equations is solved by thomas reduction algorithm O(ns).
        !
        !                       !  ds  !
        !
        ! *------*------ ... ---*------*------*------*------*--- ... -----*------*
        ! 1     is             i-2    i-1     i     i+1    i+2           ie     ns
        !
        ! options for scheme of orders between 2 and 8
        !
        ! Signature:
        ! ----------
        ! du/dx = compact_derivative(u(x), dx, nx, nxo, nxf, order)

        implicit none
        ! inputs :
        integer, intent(in) :: order
        integer, intent(in) :: ns
        double precision, intent(in) :: ds
        double precision, intent(in) :: var (ns)
        ! local :
        double precision :: rhs (ns)
        double precision :: a, b, c, d
        double precision :: ds2, ds3
        double precision :: cflux2, cflux4, bflux4
        logical :: flag
        integer :: i, n, is, ie, bs, be, ne
        ! output derivative:
        double precision, intent(out) :: ads (ns)

        rhs = 0.0
        ads = 0.0
        flag = .true.

        ds2 = 2.0 * ds
        ds3 = 3.0 * ds

        is = int(order / 2) + 1
        ie = ns + 1 - is

        bs = is - 1
        be = ie + 1

        ne = ie - is + 1

        ! compute ( u(i+1) - u(i-1) ) / 2 ds for all schemes
        call linederv(rhs, var, ds, ns, 2, ns - 1, 2)

        ! selecting coefficients for compact scheme depending on order
        scheme_order : select case (order)

        case(4) ! fourth order compact scheme o(dx^4)

            a = 1.0; b = 4.0; c = 1.0; d = 6.0

            ! store rhs of the system:
            rhs(is:ie) = d * rhs(is:ie)

        case(5, 6)
            ! 5- modified sixth order compact scheme o(dx^6). lele, (1992).

            ! define coefficients
            if (order == 5) then
                a = 2.5; b = 7.0; c = 2.5; d = 11.0
            else
                a = 3.0; b = 9.0; c = 3.0; d = 14.0
            endif

            ! compute d(u[i+1] - u[i-1])/(2ds) + b(u[i+2] - u[i-2])/(4ds)
            rhs(is:ie) = d * rhs(is:ie)

            do i = is, ie
                rhs(i) = rhs(i) + cflux2(var(i - 2), var(i + 2)) / ds2
            enddo

        case(8)
            ! modified family of sixth-order compact scheme o(dx^6). lele, (1992).

            ! define coefficients
            a = 3.0; b = 8.0; c = 3.0; d = 12.5

            ! compute d(u[i+1] - u[i-1])/(2ds) + b(u[i+2] - u[i-2])/(4ds) + c(u[i+3] - u[i-3])/(6ds)
            rhs(is:ie) = d * rhs(is:ie)

            do i = is, ie
                rhs(i) = rhs(i) + 1.6 * cflux2(var(i - 2), var(i + 2)) / ds2 &
                        & - 0.1 * cflux2(var(i - 3), var(i + 3)) / ds3
            enddo

        case default

            ! no need to solve the system,
            ! the algorithm is consistent with a=0, b=1, c=0 anyway
            ! (single diagonal matrix can be explicitly solved)
            flag = .false.

            ads = rhs

        end select scheme_order

        ! Solve system and compute derivatives at the boundaries
        if (flag) then ! (only for order /= 2)

            !---------------------------------------------------------------------------------
            ! Using fourth order explicit scheme at boundaries
            !---------------------------------------------------------------------------------

            ! Centered scheme at inner boundary points (3, bs), and (be, ns-3)
            do i = 3, bs ! has no effect for order < 4 since bs = 2
                n = be - 3 + i
                ads(i) = cflux4(var(i - 2), var(i - 1), var(i + 1), var(i + 2)) / ds
                ads(n) = cflux4(var(n - 2), var(n - 1), var(n + 1), var(n + 2)) / ds
            end do

            ! Forward-centered scheme at outer boundary points (1, 3), and (ns-2, ns)
            do i = 1, 2
                n = ns + 1 - i
                ads(i) = bflux4(var(i), var(i + 1), var(i + 2), var(i + 3), var(i + 4)) / ds
                ads(n) = - bflux4(var(n), var(n - 1), var(n - 2), var(n - 3), var(n - 4)) / ds
            end do

            !------------------------------------------------------------------------------------
            ! Solving the tridiagonal system with constant coefficients using thomas solver O(n)
            !------------------------------------------------------------------------------------
            ! Substract known values of the derivatives at the first and last row of the system
            ! (calculated with 4th order scheme)
            rhs(is) = rhs(is) - a * ads(bs)
            rhs(ie) = rhs(ie) - c * ads(be)

            ! Solve tridiagonal system for the interior grid points
            call cons_thomas(rhs(is:ie), a, b, c, ne)

            ads(is:ie) = rhs(is:ie)

        endif

        return
    end subroutine compact_derivative
    !===================================================================================================

    !===================================================================================================
    subroutine cons_thomas(v, a, b, c, ns)

        !         Thomas tridiagonal solver with constant coefficients

        implicit none
        ! inputs...
        integer, intent(in) :: ns
        double precision, intent(in) :: a, b, c

        !local temporal arrays..
        double precision :: q (2:ns)
        double precision :: r
        integer :: k

        ! input-output array ...
        double precision, intent(inout) :: v (ns)

        r = b
        v(1) = v(1) / b

        ! Foward substitution
        do k = 2, ns
            q(k) = c / r
            r = b - a * q(k)
            v(k) = (v(k) - a * v(k - 1)) / r
        enddo

        ! Backward substitution...
        do k = ns - 1, 1, -1
            v(k) = v(k) - q(k + 1) * v(k + 1)
        enddo

        return
    end subroutine cons_thomas
    !===================================================================================================


    !===================================================================================================
    subroutine gen_thomas(v, a, b, c, nn)

        !          Thomas tridiagonal solver with generic coefficients

        implicit none
        ! inputs...
        integer, intent(in) :: nn

        double precision, intent(in) :: a (nn - 1)
        double precision, intent(in) :: b (nn)
        double precision, intent(in) :: c (nn - 1)
        !local temporal arrays..
        double precision :: q (nn - 1)
        double precision :: rk
        integer :: k

        ! input-output array ...
        double precision, intent(inout) :: v (nn)

        ! matrix coefficients
        !
        ! a [ 1, 3, 4, ..., nz-1 ]  (lower diagonal)
        ! b [ 1, 2, 3, ..., nz   ]  (diagonal      )
        ! c [ 1, 3, 4, ..., nz-1 ]  (upper diagonal)
        !
        ! v  ... (rhs of system of equations / solution)

        rk = b(1)
        v(1) = v(1) / rk
        ! foward substitution ...
        do k = 1, nn - 1
            q(k) = c(k) / rk
            rk = b(k + 1) - a(k) * q(k)
            v(k + 1) = (v(k + 1) - a(k) * v(k)) / rk
        enddo

        ! backward substitution...
        do k = nn - 1, 1, -1
            v(k) = v(k) - q(k) * v(k + 1)
        enddo

        return
    end subroutine gen_thomas
    !===============================================================================================

    !===============================================================================================
    double precision function cflux2(q_im1, q_ip1)
        double precision, intent(in) :: q_im1, q_ip1
        cflux2 = (q_ip1 - q_im1) / 2.0
    end function cflux2

    double precision function cflux4(q_im2, q_im1, q_ip1, q_ip2)
        double precision, intent(in) :: q_im2, q_im1, q_ip1, q_ip2
        cflux4 = (8.d0 * (q_ip1 - q_im1) - (q_ip2 - q_im2)) / 12.0
    end function cflux4

    double precision function bflux2(q_icen, q_ipm1, q_ipm2)
        double precision, intent(in) :: q_icen, q_ipm1, q_ipm2
        bflux2 = - (3.0 * q_icen - 4.0 * q_ipm1 + q_ipm2) / 2.0
    end function bflux2

    double precision function bflux4(q_icen, q_ipm1, q_ipm2, q_ipm3, q_ipm4)
        double precision, intent(in) :: q_icen, q_ipm1, q_ipm2, q_ipm3, q_ipm4
        double precision :: coeffs(5)
        coeffs = [-25.0, 48.0, -36.0, 16.0, -3.0] / 12.0
        bflux4 = dot_product([q_icen, q_ipm1, q_ipm2, q_ipm3, q_ipm4], coeffs)
    end function bflux4

    double precision function bflux6(q_icen, q_ipm1, q_ipm2, q_ipm3, q_ipm4)
        double precision, intent(in) :: q_icen, q_ipm1, q_ipm2, q_ipm3, q_ipm4
        double precision :: coeffs(5)
        coeffs = [-25.0 / 12.0, 4.0, -3.0, 4.0 / 3.0, -1.0 / 4.0]
        bflux6 = dot_product([q_icen, q_ipm1, q_ipm2, q_ipm3, q_ipm4], coeffs)
    end function bflux6

    double precision function iflux4(q_imp1, q_icen, q_ipm1, q_ipm2, q_ipm3)
        double precision, intent(in) :: q_imp1, q_icen, q_ipm1, q_ipm2, q_ipm3
        double precision :: coeffs(5)
        coeffs = [-3.0, -10.0, 18.0, -6.0, 1.0] / 12.0
        iflux4 = dot_product([q_imp1, q_icen, q_ipm1, q_ipm2, q_ipm3], coeffs)
    end function iflux4

    !===============================================================================================
    subroutine adams_moulton(var, var0, func, ds, ns)

        implicit none

        integer, intent(in) :: ns
        double precision, intent(in) :: ds

        double precision, intent(in) :: var0
        double precision, intent(in) :: func (ns)

        double precision :: intvar
        integer :: k

        double precision, intent(out) :: var (ns)

        ! adams moulton-2 ...
        var(1) = var0
        var(2) = var(1) + ds * (func(1) + func(2)) / 2.0

        ! adams moulton-3 ...
        intvar = 5.0 * func(3) + 8.0 * func(2) - func(1)
        var(3) = var(2) + ds * intvar / 12.0

        ! adams moulton-4 ... ( foward )
        do k = 3, ns - 1

            intvar = 9.0 * func(k + 1) + 19.0 * func(k) - 5.0 * func(k - 1) + func(k - 2)
            var(k + 1) = var(k) + ds * intvar / 24.0

        end do

        return
    end subroutine adams_moulton
    !===============================================================================================


    !===============================================================================================
    subroutine adaptative_adams_moulton(var, var0, func, s, ns)
        !===============================================================================================

        implicit none

        integer, intent(in) :: ns
        double precision, intent(in) :: var0
        double precision, intent(in) :: s    (ns)
        double precision, intent(in) :: func (ns)

        double precision :: ds   (ns)
        double precision :: c1, c2, c3
        integer :: k

        double precision, intent(out) :: var (ns)

        var = 0.0

        ! calculate grid step
        ds(1:ns - 1) = s(2:ns) - s(1:ns - 1)
        ds(ns) = 0.0

        ! adams moulton-2 ...
        var(1) = var0
        var(2) = var(1) + 0.5 * ds(1) * (func(1) + func(2))

        ! adams moulton-4 ... ( foward )
        do k = 2, ns - 1

            c1 = (ds(k) / (6.0 * (ds(k) + ds(k - 1)))) * (2.0 * ds(k) + 3.0 * ds(k - 1))
            c2 = (ds(k) / (6.0 * ds(k - 1))) * (ds(k) + 3.0 * ds(k - 1))
            c3 = -(ds(k) ** 3) / (6.0 * ds(k - 1) * (ds(k) + ds(k - 1)))

            var(k + 1) = var(k) + c1 * func(k + 1) + c2 * func(k) + c3 * func(k - 1)

        end do

        return
    end subroutine adaptative_adams_moulton
    !===============================================================================================


    ! vertical integration for any f(z) ( Simpson rule is used ) ...
    !===============================================================================================
    subroutine intdomvar(intvar, var, zc, nz, n)
        !===============================================================================================

        implicit none

        integer, intent(in) :: nz, n
        double precision, intent(in) :: zc   (nz)
        double precision, intent(in) :: var  (nz)

        double precision :: vari (n)
        double precision :: dzi, z, zend

        integer :: kn, nnods
        integer :: nnstp, kstr, kend

        double precision, intent(out) :: intvar

        ! Polynomic interpolation (numnber of nodes between 3-7 is recommended.
        !                          reduces to linear interpolation for 2 nodes.)

        nnods = 5
        nnstp = nnods - 1
        kend = 1

        dzi = (zc(nz) - zc(1)) / (n - 1)
        z = dzi
        kn = 2

        vari(1) = var(1) ! extremos deben coincidir
        vari(n) = var(nz) !

        do while (kend < nz)

            kstr = kend
            kend = min(kstr + nnstp, nz)

            zend = zc(kend)

            do while (z < zend .and. kn < n)

                call lagrange_intp(vari(kn), var(kstr:kend), z, zc(kstr:kend), nnods)

                z = z + dzi
                kn = kn + 1

            end do

        end do

        !Integrating using Simpson's rule on regularly spaced data...
        intvar = (vari(1) + vari(n))

        intvar = dzi * (intvar + 4.0 * sum(vari(1:n - 1:2)) + 2.0 * sum(vari(2:n - 1:2))) / 3.0

        return
    end subroutine intdomvar
    !===============================================================================================

    !===============================================================================================
    subroutine lagrange_intp(datao, datai, posio, posii, nnods)

        ! Lagrange polynomic interpolation

        integer, intent(in) :: nnods
        double precision, intent(in) :: posio
        double precision, intent(in) :: posii(nnods)
        double precision, intent(in) :: datai(nnods)

        double precision :: lgrw
        integer :: i, j

        double precision, intent(out) :: datao

        datao = 0.0

        ! check if the data is within the interpolation range
        !if (posio >= min(posii)).and.(posio <= max(posii)) then
        !end if

        !$OMP PARALLEL DO DEFAULT(SHARED) PRIVATE(i, j, lgrw) REDUCTION(+:datao)
        do i = 1, nnods

            lgrw = 1.0

            do j = 1, nnods
                if (j /= i) then
                    lgrw = lgrw * (posio - posii(j)) / (posii(i) - posii(j))
                endif
            enddo

            datao = datao + datai(i) * lgrw

        enddo

        return
    end subroutine lagrange_intp
    !===============================================================================================
