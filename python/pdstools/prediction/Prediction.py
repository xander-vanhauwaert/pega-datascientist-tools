import datetime
import itertools
from typing import (
    TYPE_CHECKING,
    Any,
    List,
    Optional,
    Tuple,
    Union,
)

import polars as pl
import logging

from ..utils.types import QUERY
from ..utils.namespaces import LazyNamespace

from ..adm.CDH_Guidelines import CDHGuidelines
from ..utils import cdh_utils

logger = logging.getLogger(__name__)
try:
    import plotly.express as px
    import plotly.graph_objects as go

    from ..utils import pega_template as pega_template
except ImportError as e:  # pragma: no cover
    logger.debug(f"Failed to import optional dependencies: {e}")

if TYPE_CHECKING:  # pragma: no cover
    import plotly.graph_objects as go

COLORSCALE_TYPES = Union[List[Tuple[float, str]], List[str]]

Figure = Union[Any, "go.Figure"]

# T = TypeVar("T", bound="Plots")
# P = ParamSpec("P")


class PredictionPlots(LazyNamespace):
    dependencies = ["plotly"]

    def __init__(self, prediction):
        self.prediction = prediction
        super().__init__()

    def _prediction_trend(
        self,
        period: str,
        query: Optional[QUERY],
        return_df: bool,
        metric: str,
        title: str,
        facet_row: str = None,
        facet_col: str = None,
        bar_mode: bool = False,
    ):
        plot_df = self.prediction.summary_by_channel(by_period=period).with_columns(
            Prediction=pl.format("{} ({})", pl.col.Channel, pl.col.Prediction),
        )

        plot_df = cdh_utils._apply_query(plot_df, query)

        if return_df:
            return plot_df

        date_range = (
            cdh_utils._apply_query(self.prediction.predictions, query)
            .select(
                pl.format(
                    "period: {} to {}",
                    pl.col("SnapshotTime").min().dt.to_string("%v"),
                    pl.col("SnapshotTime").max().dt.to_string("%v"),
                )
            )
            .collect()
            .item()
        )

        if bar_mode:
            plt = px.bar(
                plot_df.filter(pl.col("isMultiChannelPrediction").not_())
                .filter(pl.col("Channel") != "Unknown")
                .sort("DateRange Min")
                .collect(),
                x="DateRange Min",
                y=metric,
                barmode="group",
                facet_row=facet_row,
                facet_col=facet_col,
                color="Prediction",
                title=f"{title}<br>{date_range}",
                template="pega",
            )
        else:
            plt = px.line(
                plot_df.filter(pl.col("isMultiChannelPrediction").not_())
                .filter(pl.col("Channel") != "Unknown")
                .sort("DateRange Min")
                .collect(),
                x="DateRange Min",
                y=metric,
                facet_row=facet_row,
                facet_col=facet_col,
                color="Prediction",
                title=f"{title}<br>{date_range}",
                template="pega",
                markers=True,
            )

        plt.for_each_annotation(lambda a: a.update(text="")).update_layout(
            legend_title_text="Channel"
        )

        if facet_row is not None:
            plt.update_yaxes(title="")
        if facet_col is not None:
            plt.update_xaxes(title="")

        return plt

    def performance_trend(
        self,
        period: str = "1d",
        *,
        query: Optional[QUERY] = None,
        return_df: bool = False,
    ):
        result = self._prediction_trend(
            query=query,
            period=period,
            return_df=return_df,
            metric="Performance",
            title="Prediction Performance",
        )
        if not return_df:
            result.update_yaxes(range=[50, 100])
        return result

    def lift_trend(
        self,
        period: str = "1d",
        *,
        query: Optional[QUERY] = None,
        return_df: bool = False,
    ):
        result = self._prediction_trend(
            period=period,
            query=query,
            return_df=return_df,
            metric="Lift",
            title="Prediction Lift",
        )
        if not return_df:
            result.update_yaxes(tickformat=",.2%")
        return result

    def ctr_trend(
        self,
        period: str = "1d",
        facetting=False,
        *,
        query: Optional[QUERY] = None,
        return_df: bool = False,
    ):
        result = self._prediction_trend(
            period=period,
            query=query,
            return_df=return_df,
            metric="CTR",
            title="Prediction CTR",
            facet_row="Prediction" if facetting else None,
        )
        if not return_df:
            result.update_yaxes(tickformat=",.3%")
            result.update_layout(yaxis={"rangemode": "tozero"})
        return result

    def responsecount_trend(
        self,
        period: str = "1d",
        facetting=False,
        *,
        query: Optional[QUERY] = None,
        return_df: bool = False,
    ):
        result = self._prediction_trend(
            period=period,
            query=query,
            return_df=return_df,
            metric="Responses",
            title="Prediction Responses",
            facet_col="Prediction" if facetting else None,
            bar_mode=True,
        )
        if not return_df:
            result.update_layout(yaxis_title="Responses")
        return result


class Prediction:
    """Monitor Pega Prediction Studio Predictions"""

    predictions: pl.LazyFrame
    plot: PredictionPlots

    # These are pretty strict conditions - many configurations appear not to satisfy these
    # perhaps the Total = Test + Control is no longer met when Impact Analyzer is around
    prediction_validity_expr = (
        (pl.col("Positives") > 0)
        & (pl.col("Positives_Test") > 0)
        & (pl.col("Positives_Control") > 0)
        & (pl.col("Negatives") > 0)
        & (pl.col("Negatives_Test") > 0)
        & (pl.col("Negatives_Control") > 0)
        # & (
        #     pl.col("Positives")
        #     == (pl.col("Positives_Test") + pl.col("Positives_Control"))
        # )
        # & (
        #     pl.col("Negatives")
        #     == (pl.col("Negatives_Test") + pl.col("Negatives_Control"))
        # )
    )

    def __init__(
        self,
        df: pl.LazyFrame,
        *,
        query: Optional[QUERY] = None,
    ):
        """Initialize the Prediction class

        Parameters
        ----------
        df : pl.LazyFrame
            The read in data as a Polars LazyFrame
        query : QUERY, optional
            An optional query to apply to the input data.
            For details, see :meth:`pdstools.utils.cdh_utils._apply_query`.
        """
        self.cdh_guidelines = CDHGuidelines()
        self.plot = PredictionPlots(prediction=self)

        predictions_raw_data_prepped = (
            (
                df.filter(pl.col.pyModelType == "PREDICTION")
                .with_columns(
                    # Unlike ADM we only support one pattern currently
                    SnapshotTime=pl.col("pySnapShotTime")
                    .str.slice(0, 8)
                    .str.strptime(pl.Date, "%Y%m%d"),
                    Performance=pl.col("pyValue").cast(pl.Float32),
                )
                .rename(
                    {
                        "pyPositives": "Positives",
                        "pyNegatives": "Negatives",
                        "pyCount": "ResponseCount",
                    }
                )
            )
            # collect/lazy hopefully helps to zoom in into issues
            .collect().lazy()
        )

        # Below looks like a pivot.. but we want to make sure Control, Test and NBA
        # columns are always there...
        # TODO we may want to assert that this results in exactly one record for
        # every combination of model ID and snapshot time.
        counts_control = predictions_raw_data_prepped.filter(
            pl.col.pyDataUsage == "Control"
        ).select(
            ["pyModelId", "SnapshotTime", "Positives", "Negatives", "ResponseCount"]
        )
        counts_test = predictions_raw_data_prepped.filter(
            pl.col.pyDataUsage == "Test"
        ).select(
            ["pyModelId", "SnapshotTime", "Positives", "Negatives", "ResponseCount"]
        )
        counts_NBA = predictions_raw_data_prepped.filter(
            pl.col.pyDataUsage == "NBA"
        ).select(
            ["pyModelId", "SnapshotTime", "Positives", "Negatives", "ResponseCount"]
        )

        self.predictions = (
            # Performance is taken for the records with a filled in "snapshot type".
            # The numbers of positives, negatives may not make sense but are included
            # anyways.
            predictions_raw_data_prepped.filter(pl.col.pySnapshotType == "Daily")
            .select(
                [
                    "pyModelId",
                    "SnapshotTime",
                    "Positives",
                    "Negatives",
                    "ResponseCount",
                    "Performance",
                ]
            )
            .join(counts_test, on=["pyModelId", "SnapshotTime"], suffix="_Test")
            .join(counts_control, on=["pyModelId", "SnapshotTime"], suffix="_Control")
            .join(
                counts_NBA, on=["pyModelId", "SnapshotTime"], suffix="_NBA", how="left"
            )
            .with_columns(
                Class=pl.col("pyModelId").str.extract(r"(.+)!.+"),
                ModelName=pl.col("pyModelId").str.extract(r".+!(.+)"),
                CTR=pl.col("Positives") / (pl.col("Positives") + pl.col("Negatives")),
                CTR_Test=pl.col("Positives_Test")
                / (pl.col("Positives_Test") + pl.col("Negatives_Test")),
                CTR_Control=pl.col("Positives_Control")
                / (pl.col("Positives_Control") + pl.col("Negatives_Control")),
                CTR_NBA=pl.col("Positives_NBA")
                / (pl.col("Positives_NBA") + pl.col("Negatives_NBA")),
            )
            .with_columns(
                CTR_Lift=(pl.col("CTR_Test") - pl.col("CTR_Control"))
                / pl.col("CTR_Control"),
                isValidPrediction=self.prediction_validity_expr,
            )
            .sort(["pyModelId", "SnapshotTime"])
        )

        self.predictions = cdh_utils._apply_query(self.predictions, query)

    @staticmethod
    def from_mock_data(days=70):
        n_conditions = 4  # can't change this
        n_predictions = 3  # tied to the data below
        now = datetime.datetime.now()

        def _interpolate(min, max, i, n):
            return min + (max - min) * i / (n - 1)

        mock_prediction_data = (
            pl.LazyFrame(
                {
                    "pySnapShotTime": sorted(
                        [
                            cdh_utils.to_prpc_date_time(
                                now - datetime.timedelta(days=i)
                            )[
                                0:15
                            ]  # Polars doesn't like time zones like GMT+0200
                            for i in range(days)
                        ]
                        * n_conditions
                        * n_predictions
                    ),
                    "pyModelId": (
                        [
                            "DATA-DECISION-REQUEST-CUSTOMER!PredictOutboundEmailPropensity"
                        ]
                        * n_conditions
                        + ["DATA-DECISION-REQUEST-CUSTOMER!PREDICTMOBILEPROPENSITY"]
                        * n_conditions
                        + ["DATA-DECISION-REQUEST-CUSTOMER!PREDICTWEBPROPENSITY"]
                        * n_conditions
                    )
                    * days,
                    "pyModelType": "PREDICTION",
                    "pySnapshotType": ["Daily", "Daily", "Daily", None]
                    * n_predictions
                    * days,
                    "pyDataUsage": ["Control", "Test", "NBA", ""]
                    * n_predictions
                    * days,  # Control=Random, Test=Model
                    # "pyPositives": (
                    #     [100, 160, 120, None] + [200, 420, 250, None] + [350, 700, 380, None]
                    # )
                    # * n_days,
                    "pyPositives": list(
                        itertools.chain.from_iterable(
                            [
                                [
                                    _interpolate(100, 100, p, days),
                                    _interpolate(160, 200, p, days),
                                    _interpolate(120, 120, p, days),
                                    None,
                                ]
                                + [
                                    _interpolate(120, 120, p, days),
                                    _interpolate(250, 300, p, days),
                                    _interpolate(150, 150, p, days),
                                    None,
                                ]
                                + [
                                    _interpolate(1400, 1400, p, days),
                                    _interpolate(2800, 4000, p, days),
                                    _interpolate(1520, 1520, p, days),
                                    None,
                                ]
                                for p in range(0, days)
                            ]
                        )
                    ),
                    "pyNegatives": (
                        [10000] * n_conditions
                        + [6000] * n_conditions
                        + [40000] * n_conditions
                    )
                    * days,
                    "pyValue": list(
                        itertools.chain.from_iterable(
                            [
                                [_interpolate(60.0, 65.0, p, days)] * n_conditions
                                + [_interpolate(70.0, 73.0, p, days)] * n_conditions
                                + [_interpolate(66.0, 68.0, p, days)] * n_conditions
                                for p in range(0, days)
                            ]
                        )
                    ),
                }
            ).sort(["pySnapShotTime", "pyModelId", "pySnapshotType"])
            # .with_columns(
            #     pl.col("pyPositives").cum_sum().over(["pyModelId", "pySnapshotType"]),
            #     pl.col("pyNegatives").cum_sum().over(["pyModelId", "pySnapshotType"]),
            # )
            .with_columns(pyCount=pl.col("pyPositives") + pl.col("pyNegatives"))
        )

        return Prediction(mock_prediction_data)

    @property
    def is_available(self) -> bool:
        return len(self.predictions.head(1).collect()) > 0

    @property
    def is_valid(self) -> bool:
        return (
            self.is_available
            # or even stronger: pos = pos_test + pos_control
            and self.predictions.select(self.prediction_validity_expr.all())
            .collect()
            .item()
        )

    def summary_by_channel(
        self,
        custom_predictions: Optional[List[List]] = None,
        *,
        start_date: Optional[datetime.datetime] = None,
        end_date: Optional[datetime.datetime] = None,
        window: Optional[Union[int, datetime.timedelta]] = None,
        by_period: Optional[str] = None,
        debug: bool = False,
    ) -> pl.LazyFrame:
        """Summarize prediction per channel

        Parameters
        ----------
        custom_predictions : Optional[List[CDH_Guidelines.NBAD_Prediction]], optional
            Optional list with custom prediction name to channel mappings. Defaults to None.
        start_date : datetime.datetime, optional
            Start date of the summary period. If None (default) uses the end date minus the window, or if both absent, the earliest date in the data
        end_date : datetime.datetime, optional
            End date of the summary period. If None (default) uses the start date plus the window, or if both absent, the latest date in the data
        window : int or datetime.timedelta, optional
            Number of days to use for the summary period or an explicit timedelta. If None (default) uses the whole period. Can't be given if start and end date are also given.
        by_period : str, optional
            Optional additional grouping by time period. Format string as in polars.Expr.dt.truncate (https://docs.pola.rs/api/python/stable/reference/expressions/api/polars.Expr.dt.truncate.html), for example "1mo", "1w", "1d" for calendar month, week day. Defaults to None.
        debug : bool, optional
            If True, enables debug mode for additional logging or outputs. Defaults to False.

        Returns
        -------
        pl.LazyFrame
            Summary across all Predictions as a dataframe with the following fields:

            Time and Configuration Fields:
            - DateRange Min - The minimum date in the summary time range
            - DateRange Max - The maximum date in the summary time range
            - Duration - The duration in seconds between the minimum and maximum snapshot times
            - Prediction: The prediction name
            - Channel: The channel name
            - Direction: The direction (e.g., Inbound, Outbound)
            - ChannelDirectionGroup: Combined Channel/Direction identifier
            - isValid: Boolean indicating if the prediction data is valid
            - isStandardNBADPrediction: Boolean indicating if this is a standard NBAD prediction
            - isMultiChannelPrediction: Boolean indicating if this is a multi-channel prediction
            - ControlPercentage: Percentage of responses in control group
            - TestPercentage: Percentage of responses in test group

            Performance Metrics:
            - Performance: Weighted model performance (AUC)
            - Positives: Sum of positive responses
            - Negatives: Sum of negative responses
            - Responses: Sum of all responses
            - Positives_Test: Sum of positive responses in test group
            - Positives_Control: Sum of positive responses in control group
            - Positives_NBA: Sum of positive responses in NBA group
            - Negatives_Test: Sum of negative responses in test group
            - Negatives_Control: Sum of negative responses in control group
            - Negatives_NBA: Sum of negative responses in NBA group
            - CTR: Click-through rate (Positives / (Positives + Negatives))
            - CTR_Test: Click-through rate for test group
            - CTR_Control: Click-through rate for control group
            - CTR_NBA: Click-through rate for NBA group
            - Lift: Lift value ((CTR_Test - CTR_Control) / CTR_Control)

            Technology Usage Indicators:
            - usesImpactAnalyzer: Boolean indicating if Impact Analyzer is used
        """
        if not custom_predictions:
            custom_predictions = []

        start_date, end_date = cdh_utils.get_start_end_date_args(
            self.predictions, start_date, end_date, window
        )

        query = pl.col("SnapshotTime").is_between(start_date, end_date)
        prediction_data = cdh_utils._apply_query(self.predictions, query=query, allow_empty=True)

        if by_period is not None:
            period_expr = [
                pl.col("SnapshotTime")
                .dt.truncate(by_period)
                .cast(pl.Date)
                .alias("Period")
            ]
        else:
            period_expr = []

        return (
            prediction_data.with_columns(pl.col("ModelName").str.to_uppercase())
            .join(
                self.cdh_guidelines.get_predictions_channel_mapping(
                    custom_predictions
                ).lazy(),
                left_on="ModelName",
                right_on="Prediction",
                how="left",
            )
            .rename({"ModelName": "Prediction"})
            .with_columns(
                [
                    pl.when(pl.col("Channel").is_null())
                    .then(pl.lit("Unknown"))
                    .otherwise(pl.col("Channel"))
                    .alias("Channel"),
                    pl.when(pl.col("Direction").is_null())
                    .then(pl.lit("Unknown"))
                    .otherwise(pl.col("Direction"))
                    .alias("Direction"),
                    pl.when(pl.col("isStandardNBADPrediction").is_null())
                    .then(pl.lit(False))
                    .otherwise(pl.col("isStandardNBADPrediction"))
                    .alias("isStandardNBADPrediction"),
                    pl.when(pl.col("isMultiChannelPrediction").is_null())
                    .then(pl.lit(False))
                    .otherwise(pl.col("isMultiChannelPrediction"))
                    .alias("isMultiChannelPrediction"),
                ]
                + period_expr
            )
            .group_by(
                [
                    "Prediction",
                    "Channel",
                    "Direction",
                    "isStandardNBADPrediction",
                    "isMultiChannelPrediction",
                ]
                + (["Period"] if by_period is not None else [])
            )
            .agg(
                pl.col("SnapshotTime").min().cast(pl.Date).alias("DateRange Min"),
                pl.col("SnapshotTime").max().cast(pl.Date).alias("DateRange Max"),
                (pl.col("SnapshotTime").max() - pl.col("SnapshotTime").min())
                .dt.total_seconds()
                .alias("Duration"),
                cdh_utils.weighted_performance_polars().alias("Performance"),
                pl.col("Positives").sum(),
                pl.col("Negatives").sum(),
                pl.col("ResponseCount").sum().alias("Responses"),
                pl.col("Positives_Test").sum(),
                pl.col("Positives_Control").sum(),
                pl.col("Positives_NBA").sum(),
                pl.col("Negatives_Test").sum(),
                pl.col("Negatives_Control").sum(),
                pl.col("Negatives_NBA").sum(),
            )
            .with_columns(
                usesImpactAnalyzer=(pl.col("Positives_NBA") > 0)
                & (pl.col("Negatives_NBA") > 0),
                ControlPercentage=100.0
                * (pl.col("Positives_Control") + pl.col("Negatives_Control"))
                / (
                    pl.col("Positives_Test")
                    + pl.col("Negatives_Test")
                    + pl.col("Positives_Control")
                    + pl.col("Negatives_Control")
                    + pl.col("Positives_NBA")
                    + pl.col("Negatives_NBA")
                ),
                TestPercentage=100.0
                * (pl.col("Positives_Test") + pl.col("Negatives_Test"))
                / (
                    pl.col("Positives_Test")
                    + pl.col("Negatives_Test")
                    + pl.col("Positives_Control")
                    + pl.col("Negatives_Control")
                    + pl.col("Positives_NBA")
                    + pl.col("Negatives_NBA")
                ),
                CTR=pl.col("Positives") / (pl.col("Positives") + pl.col("Negatives")),
                CTR_Test=pl.col("Positives_Test")
                / (pl.col("Positives_Test") + pl.col("Negatives_Test")),
                CTR_Control=pl.col("Positives_Control")
                / (pl.col("Positives_Control") + pl.col("Negatives_Control")),
                CTR_NBA=pl.col("Positives_NBA")
                / (pl.col("Positives_NBA") + pl.col("Negatives_NBA")),
                ChannelDirectionGroup=pl.when(
                    pl.col("Channel").is_not_null()
                    & pl.col("Direction").is_not_null()
                    & pl.col("Channel").is_in(["Other", "Unknown", ""]).not_()
                    & pl.col("Direction").is_in(["Other", "Unknown", ""]).not_()
                    & pl.col("isMultiChannelPrediction").not_()
                )
                .then(pl.concat_str(["Channel", "Direction"], separator="/"))
                .otherwise(pl.lit("Other")),
                isValid=self.prediction_validity_expr,
            )
            .with_columns(
                Lift=(pl.col("CTR_Test") - pl.col("CTR_Control"))
                / pl.col("CTR_Control"),
            )
            .drop([] if debug else ([] + ([] if by_period is None else ["Period"])))
            .sort("Prediction", "DateRange Min")
        )

    # TODO rethink use of multi-channel. If the only valid predictions are multi-channel predictions
    # then use those. If there are valid non-multi-channel predictions then only use those.
    def overall_summary(
        self,
        custom_predictions: Optional[List[List]] = None,
        *,
        start_date: Optional[datetime.datetime] = None,
        end_date: Optional[datetime.datetime] = None,
        window: Optional[Union[int, datetime.timedelta]] = None,
        by_period: Optional[str] = None,
        debug: bool = False,
    ) -> pl.LazyFrame:
        """Overall prediction summary. Only valid prediction data is included.

        Parameters
        ----------
        custom_predictions : Optional[List[CDH_Guidelines.NBAD_Prediction]], optional
            Optional list with custom prediction name to channel mappings. Defaults to None.
        start_date : datetime.datetime, optional
            Start date of the summary period. If None (default) uses the end date minus the window, or if both absent, the earliest date in the data
        end_date : datetime.datetime, optional
            End date of the summary period. If None (default) uses the start date plus the window, or if both absent, the latest date in the data
        window : int or datetime.timedelta, optional
            Number of days to use for the summary period or an explicit timedelta. If None (default) uses the whole period. Can't be given if start and end date are also given.
        by_period : str, optional
            Optional additional grouping by time period. Format string as in polars.Expr.dt.truncate (https://docs.pola.rs/api/python/stable/reference/expressions/api/polars.Expr.dt.truncate.html), for example "1mo", "1w", "1d" for calendar month, week day. Defaults to None.
        debug : bool, optional
            If True, enables debug mode for additional logging or outputs. Defaults to False.

        Returns
        -------
        pl.LazyFrame
            Summary across all Predictions as a dataframe with the following fields:

            Time and Configuration Fields:
            - DateRange Min - The minimum date in the summary time range
            - DateRange Max - The maximum date in the summary time range
            - Duration - The duration in seconds between the minimum and maximum snapshot times
            - ControlPercentage: Weighted average percentage of control group responses
            - TestPercentage: Weighted average percentage of test group responses

            Performance Metrics:
            - Performance: Weighted average performance across all valid channels
            - Positives Inbound: Sum of positive responses across all valid inbound channels
            - Positives Outbound: Sum of positive responses across all valid outbound channels
            - Responses Inbound: Sum of all responses across all valid inbound channels
            - Responses Outbound: Sum of all responses across all valid outbound channels
            - Overall Lift: Weighted average lift across all valid channels
            - Minimum Negative Lift: The lowest negative lift value found

            Channel Statistics:
            - Number of Valid Channels: Count of unique valid channel/direction combinations
            - Channel with Minimum Negative Lift: Channel with the lowest negative lift value

            Technology Usage Indicators:
            - usesImpactAnalyzer: Boolean indicating if any channel uses Impact Analyzer
        """

        # start_date, end_date = cdh_utils.get_start_end_date_args(
        #     self.datamart.model_data, start_date, end_date, window
        # )

        channel_summary = self.summary_by_channel(
            custom_predictions=custom_predictions,
            start_date=start_date,
            end_date=end_date,
            window=window,
            by_period=by_period,
            debug=True,  # should give us Period
        )

        if (
            channel_summary.select(
                (pl.col("isMultiChannelPrediction").not_() & pl.col("isValid")).any()
            )
            .collect()
            .item()
        ):
            # There are valid non-multi-channel predictions
            validity_filter_expr = pl.col("isMultiChannelPrediction").not_() & pl.col(
                "isValid"
            )
        else:
            validity_filter_expr = pl.col("isValid")

        return (
            channel_summary.filter(validity_filter_expr)
            .group_by(["Period"] if by_period is not None else None)
            .agg(
                pl.col("DateRange Min").min(),
                pl.col("DateRange Max").max(),
                pl.col("Duration").max(),
                pl.concat_str(["Channel", "Direction"], separator="/")
                .n_unique()
                .alias("Number of Valid Channels"),
                cdh_utils.weighted_average_polars("Lift", "Responses").alias(
                    "Overall Lift"
                ),
                cdh_utils.weighted_performance_polars("Performance", "Responses").alias(
                    "Performance"
                ),
                pl.col("Positives")
                .filter(Direction="Inbound")
                .sum()
                .alias("Positives Inbound"),
                pl.col("Positives")
                .filter(Direction="Outbound")
                .sum()
                .alias("Positives Outbound"),
                pl.col("Responses")
                .filter(Direction="Inbound")
                .sum()
                .alias("Responses Inbound"),
                pl.col("Responses")
                .filter(Direction="Outbound")
                .sum()
                .alias("Responses Outbound"),
                pl.col("Channel")
                .filter((pl.col("Lift") == pl.col("Lift").min()) & (pl.col("Lift") < 0))
                .first()
                .alias("Channel with Minimum Negative Lift"),
                pl.col("Lift")
                .filter((pl.col("Lift") == pl.col("Lift").min()) & (pl.col("Lift") < 0))
                .first()
                .alias("Minimum Negative Lift"),
                pl.col("usesImpactAnalyzer"),
                cdh_utils.weighted_average_polars(
                    "ControlPercentage", "Responses"
                ).alias("ControlPercentage"),
                cdh_utils.weighted_average_polars("TestPercentage", "Responses").alias(
                    "TestPercentage"
                ),
            )
            .drop(["literal"] if by_period is None else [])  # created by null group
            .with_columns(
                # CTR=(pl.col("Positives")) / (pl.col("Responses")),
                usesImpactAnalyzer=pl.col("usesImpactAnalyzer").list.any(),
            )
            .drop([] if debug else ([] + ([] if by_period is None else ["Period"])))
            .sort("DateRange Min")
        )
