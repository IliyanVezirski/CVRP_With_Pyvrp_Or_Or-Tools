import unittest

from current_tsp import (
    _open_route_cost,
    _solve_open_tsp_order,
    _two_opt_open_route,
)
from input_handler import Customer
from osrm_client import DistanceMatrix


def _customer(node: int, time_windows=None) -> Customer:
    return Customer(
        id=str(node),
        name=f"Customer {node}",
        coordinates=(42.0 + node / 1000, 23.0),
        volume=1.0,
        original_gps_data="",
        time_windows=list(time_windows or []),
    )


def _matrix(values):
    size = len(values)
    return DistanceMatrix(
        distances=[list(row) for row in values],
        durations=[list(row) for row in values],
        locations=[(42.0 + index / 1000, 23.0) for index in range(size)],
        sources=list(range(size)),
        destinations=list(range(size)),
    )


def _solve(customers, matrix, *, end_node=None, use_time_windows=False, two_opt_enabled=True):
    return _solve_open_tsp_order(
        customers,
        matrix,
        metric="distance",
        start_time_minutes=0,
        service_time_minutes=0,
        use_time_windows=use_time_windows,
        wait_weight=1,
        late_weight=20,
        two_opt_enabled=two_opt_enabled,
        two_opt_max_passes=30,
        end_node=end_node,
    )


class FixedEndTspTests(unittest.TestCase):
    def setUp(self):
        # Node 0 is the start, 1/2 are customers, and 3 is the fixed end.
        # Historical nearest-neighbour selected [1, 2]: 1 + 100 + 1 = 102.
        # The correct fixed-end order is [2, 1]: 2 + 0 + 1 = 3.
        self.two_customer_matrix = _matrix(
            [
                [0, 1, 2, 50],
                [1, 0, 100, 1],
                [2, 0, 0, 1],
                [50, 1, 1, 0],
            ]
        )
        self.two_customers = [_customer(1), _customer(2)]

    def test_fixed_end_participates_in_initial_order_without_local_search(self):
        _, order = _solve(
            self.two_customers,
            self.two_customer_matrix,
            end_node=3,
            two_opt_enabled=False,
        )

        self.assertEqual([2, 1], order)
        self.assertEqual(
            3,
            _open_route_cost(order, self.two_customer_matrix, "distance", end_node=3),
        )

    def test_local_improvement_fixes_two_customer_order(self):
        improved = _two_opt_open_route(
            [1, 2],
            self.two_customers,
            self.two_customer_matrix,
            "distance",
            start_time_minutes=0,
            service_time_minutes=0,
            use_time_windows=False,
            wait_weight=1,
            late_weight=20,
            end_node=3,
            max_passes=30,
        )

        self.assertEqual([2, 1], improved)
        self.assertEqual(
            3,
            _open_route_cost(improved, self.two_customer_matrix, "distance", end_node=3),
        )

    def test_fixed_end_local_improvement_handles_one_and_three_stops(self):
        single = _two_opt_open_route(
            [1],
            [_customer(1)],
            _matrix([[0, 1, 9], [1, 0, 1], [9, 1, 0]]),
            "distance",
            start_time_minutes=0,
            service_time_minutes=0,
            use_time_windows=False,
            wait_weight=1,
            late_weight=20,
            end_node=2,
            max_passes=30,
        )
        self.assertEqual([1], single)

        three_stop_matrix = _matrix(
            [
                [0, 50, 50, 1, 50],
                [50, 0, 50, 50, 1],
                [50, 1, 0, 50, 50],
                [50, 50, 1, 0, 50],
                [50, 50, 50, 50, 0],
            ]
        )
        improved = _two_opt_open_route(
            [1, 2, 3],
            [_customer(1), _customer(2), _customer(3)],
            three_stop_matrix,
            "distance",
            start_time_minutes=0,
            service_time_minutes=0,
            use_time_windows=False,
            wait_weight=1,
            late_weight=20,
            end_node=4,
            max_passes=30,
        )
        self.assertEqual([3, 2, 1], improved)
        self.assertEqual(4, _open_route_cost(improved, three_stop_matrix, "distance", end_node=4))

    def test_open_route_without_end_keeps_nearest_neighbour_behaviour(self):
        _, order = _solve(
            self.two_customers,
            self.two_customer_matrix,
            end_node=None,
            two_opt_enabled=True,
        )

        self.assertEqual([1, 2], order)

    def test_time_window_penalty_is_used_with_fixed_end(self):
        matrix = _matrix(
            [
                [0, 1, 2, 50],
                [1, 0, 100, 1],
                [2, 70, 0, 1],
                [50, 1, 1, 0],
            ]
        )
        customers = [_customer(1, [(0, 1)]), _customer(2)]

        _, without_windows = _solve(
            customers,
            matrix,
            end_node=3,
            use_time_windows=False,
            two_opt_enabled=False,
        )
        _, with_windows = _solve(
            customers,
            matrix,
            end_node=3,
            use_time_windows=True,
            two_opt_enabled=False,
        )

        self.assertEqual([2, 1], without_windows)
        self.assertEqual([1, 2], with_windows)


if __name__ == "__main__":
    unittest.main()
