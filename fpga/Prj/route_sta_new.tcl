# New source route: requires the completed MAP outputs from rebuild_sources.cmd.
# Does not generate a bitstream. Reports require independent review.
set project_dir [file dirname [file normalize [info script]]]
cd $project_dir
set_arch -family Logos2 -device PG2L200H -speedgrade -6 -package FBB676
add_constraint [file join $project_dir {build_from_source/map/device_map/meeting_av_mini200.pcf}]
pnr -mode performance -number_of_router_thread 5 -fix_hold_violation_in_route TRUE -fix_hold_violation_threshold_for_clock_path_cross_srb 20 -fix_hold_violation_threshold_for_clk_tree 20 -max_hold_violated_paths_num 60000 -force_to_run -input_db_file [file join $project_dir {build_from_source/map/device_map/meeting_av_mini200_map.adf}] -generate_bitstream FALSE
report_timing -delay_type min_max -report_max_skew -report_io_datasheet -report_exception TRUE -nworst 20 -max_path 200
