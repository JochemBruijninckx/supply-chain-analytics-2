import gurobipy as gb
import numpy as np


class Model:
    def __init__(self, problem, settings=None, bounds=None, surpress_logs=False, parameters=None):
        if settings is None:
            settings = {}
        for setting in ['all_links_open', 'non_integer_trucks', 'perfect_delivery', 'linear_backlog_approx']:
            if setting not in settings.keys():
                settings[setting] = False
        if bounds is None:
            bounds = {}
        if parameters is None:
            parameters = {}

        # Model setup
        # --------------------------------------------------------------------------------------
        mdl = gb.Model()
        if surpress_logs:
            mdl.setParam("OutputFlag", 0)

        # Index sets
        if not problem.random:
            link_product_time = problem.link_product_time
            supplier_product_time = problem.supplier_product_time
            link_time = problem.link_time
            customer_product_time = problem.customer_product_time
            dc_product_time = problem.dc_product_time
        else:
            N = len(problem.scenarios)
            link_product_time = [(i, j, p, t, theta) for i, j, p, t in problem.link_product_time for theta in range(N)]
            supplier_product_time = [(s, p, t, theta) for s, p, t in problem.supplier_product_time
                                     for theta in range(N)]
            link_time = [(i, j, t, theta) for i, j, t in problem.link_time for theta in range(N)]
            customer_product_time = [(c, p, t, theta) for c, p, t in problem.customer_product_time
                                     for theta in range(N)]
            dc_product_time = [(i, p, t, theta) for i, p, t in problem.dc_product_time
                               for theta in range(N)]

        # Variables
        # --------------------------------------------------------------------------------------
        x = mdl.addVars(link_product_time, vtype=gb.GRB.CONTINUOUS, lb=0, name='x')
        if not problem.random:
            r = mdl.addVars(supplier_product_time, vtype=gb.GRB.BINARY, name='r')
        if settings['all_links_open']:
            l = mdl.addVars(problem.links, vtype=gb.GRB.BINARY, name='l', lb=1, ub=1)
        else:
            l = mdl.addVars(problem.links, vtype=gb.GRB.BINARY, name='l')
        if settings['non_integer_trucks']:
            k = mdl.addVars(link_time, vtype=gb.GRB.CONTINUOUS, lb=0, name='k')
            v = mdl.addVars(problem.links, vtype=gb.GRB.CONTINUOUS, lb=0, name='v')
        else:
            k = mdl.addVars(link_time, vtype=gb.GRB.INTEGER, lb=0, name='k')
            v = mdl.addVars(problem.links, vtype=gb.GRB.INTEGER, lb=0, name='v')
        if settings['linear_backlog_approx'] or problem.random:
            z = mdl.addVars(customer_product_time, vtype=gb.GRB.CONTINUOUS, lb=0, name='z')

        I = mdl.addVars(dc_product_time, vtype=gb.GRB.CONTINUOUS, lb=0, name='I')

        # Set bounds if provided
        if 'v' in bounds:
            for (i, j) in bounds['v'].keys():
                if 'lb' in bounds['v'][(i, j)].keys():
                    v[i, j].lb = bounds['v'][(i, j)]['lb']
                if 'ub' in bounds['v'][(i, j)].keys():
                    v[i, j].ub = bounds['v'][(i, j)]['ub']

        # Objective
        # --------------------------------------------------------------------------------------
        tot_opening_cost = gb.LinExpr()
        tot_capacity_cost = gb.LinExpr()
        tot_distance_cost = gb.LinExpr()
        tot_holding_cost = gb.LinExpr()
        tot_backlog_cost = gb.QuadExpr()

        # Opening costs
        tot_opening_cost += gb.quicksum(problem.opening_cost[i, j] * l[i, j] for i, j in problem.links)

        # Capacity costs
        tot_capacity_cost += gb.quicksum(problem.capacity_cost[i, j] * v[i, j] for i, j in problem.links)

        if not problem.random:
            # Distance costs
            tot_distance_cost += gb.quicksum(problem.distance[i, j] * k[i, j, t] for i, j, t in problem.link_time)

            # Holding costs
            tot_holding_cost += gb.quicksum(problem.holding_cost[d] * gb.quicksum(problem.product_volume[p] * I[d, p, t]
                                                                                  for p in problem.P)
                                            for d, t in problem.depot_time)

            # Backlog costs
            if not settings['perfect_delivery']:
                if settings['linear_backlog_approx']:
                    tot_backlog_cost += gb.quicksum(problem.backlog_pen[c, p] * z[c, p, t]
                                                    for c, p, t in problem.customer_product_time)
                else:
                    tot_backlog_cost += gb.quicksum(
                        problem.backlog_pen[c, p] * (I[c, p, t] - problem.cum_demand[c, p, t]) ** 2
                        for c, p, t in problem.customer_product_time)
        else:
            # Distance costs
            tot_distance_cost += (1 / N) * gb.quicksum(gb.quicksum(problem.distance[i, j] * k[i, j, t, theta]
                                                                   for i, j, t in problem.link_time)
                                                       for theta in range(N))
            # Holding costs
            tot_holding_cost += (1 / N) * gb.quicksum(
                gb.quicksum(problem.holding_cost[d] *
                            gb.quicksum(problem.product_volume[p] * I[d, p, t, theta] for p in problem.P)
                            for d, t in problem.depot_time) for theta in range(N))

            # Backlog costs
            tot_backlog_cost += (1 / N) * gb.quicksum(gb.quicksum(problem.backlog_pen[c, p] * z[c, p, t, theta]
                                                                  for c, p, t in problem.customer_product_time) for
                                                      theta in range(N))

        mdl.setObjective(tot_opening_cost + tot_capacity_cost + tot_distance_cost + tot_holding_cost + tot_backlog_cost,
                         gb.GRB.MINIMIZE)

        # Constraints
        # --------------------------------------------------------------------------------------
        # Linking constraint for opening of links
        mdl.addConstrs(
            (10000 * l[i, j] >= v[i, j] for i, j in problem.links),
            name='Links must be opened to procure capacity'
        )

        if not problem.random:
            # Truck capacity on links
            mdl.addConstrs(
                (k[i, j, t] <= v[i, j] for i, j, t in link_time),
                name='# Trucks cannot exceed capacity'
            )
            # Sufficient amount of trucks for transport size
            mdl.addConstrs(
                (k[i, j, t] >= gb.quicksum(problem.product_volume[p] * x[i, j, p, t]
                                           for p in problem.P) / problem.truck_size
                 for i, j, t in problem.link_time),
                name='# Trucks required for transport volume'
            )
            # Minimum production constraint for suppliers
            mdl.addConstrs(
                (gb.quicksum(x[s, j, p, t] for j in problem.D_and_C if (s, j) in problem.links) >=
                 problem.min_prod[s, p] * r[s, p, t] for s, p, t in problem.supplier_product_time),
                name='Minimum required production if supplier used'
            )
            # Maximum production constraint for suppliers
            mdl.addConstrs(
                (gb.quicksum(x[s, j, p, t] for j in problem.D_and_C if (s, j) in problem.links) <=
                 problem.max_prod[s, p] * r[s, p, t] for s, p, t in problem.supplier_product_time),
                name='Maximum allowed production if supplier used'
            )
            # Capacity constraint for depots
            mdl.addConstrs(
                (gb.quicksum(problem.product_volume[p] * I[d, p, t] for p in problem.P) <= problem.capacity[d]
                 for d, t in problem.depot_time),
                name='Depot inventory volume cannot exceed capacity'
            )
            # Cannot transport more from depots than is in their inventories
            mdl.addConstrs(
                (gb.quicksum(x[d, j, p, t] for j in problem.D_and_C if (d, j) in problem.links) <=
                 I[d, p, t - 1] + gb.quicksum(x[j, d, p, t - problem.duration[j, d]] for j in problem.S_and_D
                                              if
                                              (j, d) in problem.links and t - problem.duration[j, d] >= problem.start)
                 for d, p, t in problem.depot_product_time),
                name='Outgoing transport from depot cannot exceed inventory'
            )
            # Flow constraints
            mdl.addConstrs(
                (I[d, p, t] == I[d, p, t - 1]
                 + gb.quicksum(x[j, d, p, t - problem.duration[j, d]] for j in problem.S_and_D
                               if (j, d) in problem.links and t - problem.duration[j, d] >= problem.start)
                 - gb.quicksum(x[d, j, p, t] for j in problem.D_and_C if (d, j) in problem.links)
                 for d, p, t in problem.depot_product_time),
                name='Depot inventory flow constraint'
            )
            mdl.addConstrs(
                (I[c, p, t] == I[c, p, t - 1]
                 + gb.quicksum(x[i, c, p, t - problem.duration[i, c]] for i in problem.S_and_D
                               if t - problem.duration[i, c] >= problem.start and (i, c) in problem.links)
                 for c, p, t in problem.customer_product_time),
                name='Customer inventory flow constraint'
            )
            # Nodes start at zero inventory
            mdl.addConstrs(
                (I[i, p, 0] == 0 for i in problem.D_and_C for p in problem.P),
                name='Nodes start at zero inventory'
            )
            # All demand must be filled by end of period
            mdl.addConstrs(
                (I[c, p, problem.end] == problem.cum_demand[c, p, problem.end] for c, p in problem.customer_product),
                name='Final customer inventory must match cumulative demand'
            )

            if settings['perfect_delivery']:
                mdl.addConstrs(
                    (I[c, p, t] == problem.cum_demand[c, p, t] for c, p, t in problem.customer_product_time),
                    name='Perfect delivery constraint'
                )

            # Tangent line constraints in case of linear backlog approximation
            if settings['linear_backlog_approx']:
                boundary = parameters['boundary'] if 'boundary' in parameters.keys() else 5
                delta = parameters['delta'] if 'delta' in parameters.keys() else 1
                mdl.addConstrs(
                    (z[c, p, t] >= 2 * w * (I[c, p, t] - problem.cum_demand[c, p, t]) - w ** 2
                     for c, p, t in problem.customer_product_time
                     for w in np.arange(-boundary, boundary + delta, delta) if w != 0)
                )
                mdl.addConstrs(
                    (z[c, p, t] >= w * (I[c, p, t] - problem.cum_demand[c, p, t])
                     for c, p, t in problem.customer_product_time
                     for w in [-delta, delta])
                )
        else:
            # Truck capacity on links
            mdl.addConstrs(
                (k[i, j, t, theta] <= v[i, j] for i, j, t, theta in link_time),
                name='# Trucks cannot exceed capacity'
            )
            # Sufficient amount of trucks for transport size
            mdl.addConstrs(
                (k[i, j, t, theta] >= gb.quicksum(problem.product_volume[p] * x[i, j, p, t, theta]
                                                  for p in problem.P) / problem.truck_size
                 for i, j, t, theta in link_time),
                name='# Trucks required for transport volume'
            )
            # Maximum production constraint for suppliers
            mdl.addConstrs(
                (gb.quicksum(x[s, j, p, t, theta] for j in problem.D_and_C if (s, j) in problem.links) <=
                 problem.max_prod[s, p] * problem.scenarios[theta]['availability'][s, p, t]
                 for s, p, t, theta in supplier_product_time if (s, p) in problem.supplier_product),
                name='Maximum allowed production if supplier used'
            )
            mdl.addConstrs(
                (gb.quicksum(x[s, j, p, t, theta] for j in problem.D_and_C if (s, j) in problem.links) <= 0
                 for s, p, t, theta in supplier_product_time if (s, p) not in problem.supplier_product),
                name='Maximum allowed production if supplier used'
            )
            # Capacity constraint for depots
            mdl.addConstrs(
                (gb.quicksum(problem.product_volume[p] * I[d, p, t, theta] for p in problem.P) <= problem.capacity[d]
                 for d, t in problem.depot_time for theta in range(N)),
                name='Depot inventory volume cannot exceed capacity'
            )
            # Cannot transport more from depots than is in their inventories
            mdl.addConstrs(
                (gb.quicksum(x[d, j, p, t, theta] for j in problem.D_and_C if (d, j) in problem.links) <=
                 I[d, p, t - 1, theta] + gb.quicksum(x[j, d, p, t - problem.duration[j, d], theta]
                                                     for j in problem.S_and_D if (j, d) in problem.links
                                                     and t - problem.duration[j, d] >= problem.start)
                 for d, p, t in problem.depot_product_time for theta in range(N)),
                name='Outgoing transport from depot cannot exceed inventory'
            )
            # Flow constraints
            mdl.addConstrs(
                (I[d, p, t, theta] == I[d, p, t - 1, theta]
                 + gb.quicksum(x[j, d, p, t - problem.duration[j, d], theta] for j in problem.S_and_D
                               if (j, d) in problem.links and t - problem.duration[j, d] >= problem.start)
                 - gb.quicksum(x[d, j, p, t, theta] for j in problem.D_and_C if (d, j) in problem.links)
                 for d, p, t in problem.depot_product_time for theta in range(N)),
                name='Depot inventory flow constraint'
            )
            mdl.addConstrs(
                (I[c, p, t, theta] == I[c, p, t - 1, theta]
                 + gb.quicksum(x[i, c, p, t - problem.duration[i, c], theta] for i in problem.S_and_D
                               if t - problem.duration[i, c] >= problem.start and (i, c) in problem.links)
                 for c, p, t in problem.customer_product_time for theta in range(N)),
                name='Customer inventory flow constraint'
            )
            # Nodes start at zero inventory
            mdl.addConstrs(
                (I[i, p, 0, theta] == 0 for i in problem.D_and_C for p in problem.P for theta in range(N)),
                name='Nodes start at zero inventory'
            )

            if settings['perfect_delivery']:
                mdl.addConstrs(
                    (I[c, p, t, theta] == problem.scenarios[theta]['cum_demand'][c, p, problem.end]
                     for c, p, t in problem.customer_product_time for theta in range(N)),
                    name='Perfect delivery constraint'
                )

            mdl.addConstrs(
                (z[c, p, t, theta] >= w * (I[c, p, t, theta] - problem.scenarios[theta]['cum_demand'][c, p, t])
                 for c, p, t in problem.customer_product_time
                 for w in [-1, 1] for theta in range(N))
            )

        # Generate model
        mdl.update()
        self.mdl = mdl

    # Solve model and save solution to a solution file
    def solve(self, instance_name=None, stopping_criteria=None):
        if stopping_criteria is not None:
            for key, value in stopping_criteria.items():
                if key == 'objective':
                    self.mdl.setParam('BestObjStop', value)
                elif key == 'bound':
                    self.mdl.setParam('BestBdStop', value)
                elif key == 'gap':
                    self.mdl.setParam('MIPGap', value)
                elif key == 'time':
                    self.mdl.setParam('TimeLimit', value)
        # Optimize
        self.mdl.optimize()
        if self.mdl.status not in [2, 9, 11, 15] or self.mdl.getAttr('SolCount') == 0:
            return np.inf
        # Save solution
        if instance_name:
            self.mdl.write('Solutions/' + instance_name + '.sol')
        # Return objective value
        return self.mdl.getObjective().getValue()

    def write(self, instance_name):
        self.mdl.write('Instances/' + instance_name + '.lp')

    def save_solution(self, instance_name):
        self.mdl.write('Solutions/' + instance_name + '.sol')
