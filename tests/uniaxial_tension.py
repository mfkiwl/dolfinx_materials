import numpy as np
import ufl
from mpi4py import MPI
from dolfinx import fem, mesh, io

from dolfinx_materials.quadrature_map import QuadratureMap

from dolfinx_materials.solvers import NonlinearMaterialProblem
from dolfinx.cpp.nls.petsc import NewtonSolver  # noqa


def uniaxial_tension_2D(material, Exx, N=1, order=1, save_fields=None):
    domain = mesh.create_unit_square(MPI.COMM_WORLD, N, N, mesh.CellType.quadrilateral)
    V = fem.functionspace(domain, ("P", order, (2,)))

    deg_quad = 2 * (order - 1)

    def bottom(x):
        return np.isclose(x[1], 0)

    def left(x):
        return np.isclose(x[0], 0)

    def right(x):
        return np.isclose(x[0], 1.0)

    V_ux, _ = V.sub(0).collapse()
    left_dofs_ux = fem.locate_dofs_geometrical((V.sub(0), V_ux), left)
    right_dofs_ux = fem.locate_dofs_geometrical((V.sub(0), V_ux), right)
    V_uy, _ = V.sub(1).collapse()
    bottom_dofs_uy = fem.locate_dofs_geometrical((V.sub(1), V_uy), bottom)

    uD_x = fem.Function(V_ux)
    uD_y = fem.Function(V_uy)
    uD_x_r = fem.Function(V_ux)
    bcs = [
        fem.dirichletbc(uD_x, left_dofs_ux, V.sub(0)),
        fem.dirichletbc(uD_y, bottom_dofs_uy, V.sub(1)),
        fem.dirichletbc(uD_x_r, right_dofs_ux, V.sub(0)),
    ]

    du = ufl.TrialFunction(V)
    v = ufl.TestFunction(V)
    u = fem.Function(V)

    def strain(u):
        return ufl.as_vector(
            [
                u[0].dx(0),
                u[1].dx(1),
                0.0,
                1 / np.sqrt(2) * (u[1].dx(0) + u[0].dx(1)),
                0.0,
                0.0,
            ]
        )

    qmap = QuadratureMap(domain, deg_quad, material)
    qmap.register_gradient(material.gradient_names[0], strain(u))

    sig = qmap.fluxes[material.flux_names[0]]
    Res = ufl.dot(sig, strain(v)) * qmap.dx
    Jac = qmap.derivative(Res, u, du)

    problem = NonlinearMaterialProblem(qmap, Res, Jac, u, bcs)
    newton = NewtonSolver(MPI.COMM_WORLD)
    newton.rtol = 1e-6
    newton.max_it = 10

    file_results = io.XDMFFile(
        domain.comm,
        f"{material.name}_results.xdmf",
        "w",
    )
    file_results.write_mesh(domain)
    Stress = np.zeros((len(Exx), 6))
    for i, exx in enumerate(Exx[1:]):
        uD_x_r.x.array[:] = exx

        converged, _ = problem.solve(newton)

        assert converged
        Stress[i + 1, :] = sig.x.array[:6]

        if save_fields is not None:
            for field_name in save_fields:
                field = qmap.project_on(field_name, ("DG", 0))
                file_results.write_function(field, i)

    file_results.close()
    return Stress
