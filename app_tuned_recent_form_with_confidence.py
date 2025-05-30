import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score
import warnings

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore', category=pd.errors.SettingWithCopyWarning)
warnings.filterwarnings('ignore', category=UserWarning)

# Load the dataset
data = pd.read_csv('pl-tables-1993-2023.csv')

# Define features and target
features = ['points', 'gf', 'ga', 'gd', 'won', 'drawn', 'lost']
target = 'position'

# Extended features will include recent_form_score when making predictions
extended_features = features + ['recent_form_score']

X = data[features]  # Feature columns
y = data[target]    # Target column (Position)

# Function to calculate weighted average with recent form
def weighted_average(team_data, features, current_year):
    # Create a copy to avoid SettingWithCopyWarning
    team_data = team_data.copy()
    
    # Assign weights inversely proportional to the difference from the current year
    team_data['year_diff'] = current_year - team_data['season_end_year']
    
    # Calculate weights, giving more weight to recent seasons
    team_data['weight'] = np.exp(-team_data['year_diff'] / 1.05)
    
    # Calculate weighted average for each feature
    weighted_avg = {}
    for feature in features:
        weighted_avg[feature] = np.average(team_data[feature], weights=team_data['weight'])
    
    # Add recent form score as a new feature
    recent_form = calculate_recent_form_score(team_data, current_year)
    weighted_avg['recent_form_score'] = recent_form
    
    return pd.Series(weighted_avg)

# Function to calculate recent form analysis (tuned version)
def calculate_recent_form_score(team_data, current_year, last_n_seasons=3):
    """
    Calculate performance trend in most recent seasons to capture momentum.
    Returns a form score indicating if team is improving (+) or declining (-).
    """
    # Get data from recent seasons only
    recent_data = team_data[team_data['season_end_year'] >= (current_year - last_n_seasons)]
    
    if len(recent_data) < 2:
        return 0  # Not enough data to calculate trend
    
    # Sort by year to ensure proper trend calculation
    recent_data = recent_data.sort_values('season_end_year')
    
    # Calculate trend in points over recent seasons
    if len(recent_data) >= 2:
        try:
            # Normalize years to start from 0 for better polynomial fitting
            years_normalized = recent_data['season_end_year'] - recent_data['season_end_year'].min()
            
            # Add small noise to prevent singular matrix issues
            if len(np.unique(years_normalized)) < 2:
                years_normalized = years_normalized + np.random.normal(0, 0.01, len(years_normalized))
            
            # Calculate both trends with better scaling and error handling
            points_trend = np.polyfit(years_normalized, recent_data['points'], 1)[0]
            position_trend = -np.polyfit(years_normalized, recent_data['position'], 1)[0]
            
            # Scale the trends to reasonable ranges
            points_trend_scaled = points_trend / 5
            position_trend_scaled = position_trend / 2
            
            # Combine both trends with balanced weighting
            form_score = (points_trend_scaled * 0.6) + (position_trend_scaled * 0.4)
            
            # Cap the form score to prevent extreme adjustments
            form_score = max(-3, min(3, form_score))
            
            return form_score
        except (np.linalg.LinAlgError, RuntimeWarning):
            # If polynomial fitting fails, return 0 (no trend)
            return 0
    
    return 0

# Bootstrap sampling function for confidence intervals
def bootstrap_confidence_interval_recent_form(historical_data, team_data, features, prediction_year, n_bootstrap=1000, confidence_level=0.95):
    """
    Calculate confidence intervals using bootstrap sampling for recent form model
    
    Args:
        historical_data: Historical dataset
        team_data: Historical data for specific team
        features: Features used for prediction
        prediction_year: Year being predicted
        n_bootstrap: Number of bootstrap samples
        confidence_level: Confidence level for intervals (e.g., 0.95 for 95%)
    
    Returns:
        dict: Contains prediction, lower_bound, upper_bound, standard_error
    """
    bootstrap_predictions = []
    
    for i in range(n_bootstrap):
        # Create bootstrap sample by sampling with replacement
        bootstrap_sample = historical_data.sample(n=len(historical_data), replace=True, random_state=i)
        
        # Train model on bootstrap sample
        X_boot = bootstrap_sample[features]
        y_boot = bootstrap_sample[target]
        
        rf_boot = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_boot.fit(X_boot, y_boot)
        
        # Get team's bootstrap sample for trend calculation
        team_bootstrap = bootstrap_sample[bootstrap_sample['team'] == team_data['team'].iloc[0]]
        
        if len(team_bootstrap) > 0:
            # Calculate weighted average features
            trend_features = weighted_average(team_bootstrap, features, prediction_year)
            X_team = trend_features[features].values.reshape(1, -1)
            
            # Make prediction
            predicted_pos = rf_boot.predict(X_team)[0]
            
            # Apply recent form adjustment
            form_score = trend_features['recent_form_score']
            form_adjustment = -form_score * 0.05
            adjusted_prediction = predicted_pos + form_adjustment
            adjusted_prediction = max(1, min(20, adjusted_prediction))
            
            bootstrap_predictions.append(adjusted_prediction)
    
    # Calculate confidence intervals
    alpha = 1 - confidence_level
    lower_percentile = (alpha / 2) * 100
    upper_percentile = (1 - alpha / 2) * 100
    
    prediction = np.mean(bootstrap_predictions)
    lower_bound = np.percentile(bootstrap_predictions, lower_percentile)
    upper_bound = np.percentile(bootstrap_predictions, upper_percentile)
    standard_error = np.std(bootstrap_predictions)
    
    return {
        'prediction': prediction,
        'lower_bound': lower_bound,
        'upper_bound': upper_bound,
        'standard_error': standard_error,
        'confidence_level': confidence_level
    }

# Cross-validation function for time-series validation
def cross_validate_time_series(data, features, target, prediction_year):
    """
    Perform time-series cross-validation using chronological splits.
    Returns average CV score across multiple time periods.
    """
    # Define validation periods (train on earlier data, test on later)
    cv_periods = [
        {'train_end': 2013, 'test_start': 2014, 'test_end': 2016},
        {'train_end': 2016, 'test_start': 2017, 'test_end': 2019},
        {'train_end': 2019, 'test_start': 2020, 'test_end': 2023}
    ]
    
    cv_scores = []
    
    for period in cv_periods:
        # Create training and testing sets based on time periods
        train_data = data[data['season_end_year'] <= period['train_end']]
        test_data = data[(data['season_end_year'] >= period['test_start']) & 
                        (data['season_end_year'] <= period['test_end'])]
        
        if len(train_data) == 0 or len(test_data) == 0:
            continue
            
        # Prepare training data
        X_train_cv = train_data[features]
        y_train_cv = train_data[target]
        
        # Train model on this fold
        rf_cv = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_cv.fit(X_train_cv, y_train_cv)
        
        # Get unique teams from test period
        test_teams = test_data['team'].unique()
        
        # Make predictions for each team in test period
        fold_predictions = []
        fold_actuals = []
        
        for team in test_teams:
            team_test_data = test_data[test_data['team'] == team]
            
            for _, row in team_test_data.iterrows():
                # Get historical data for this team (before the test year)
                team_historical = train_data[train_data['team'] == team]
                
                if len(team_historical) == 0:
                    continue
                
                # Calculate weighted average for this team
                trend_features = weighted_average(team_historical, features, row['season_end_year'])
                X_team = trend_features[features].values.reshape(1, -1)
                
                # Make prediction
                predicted_pos = rf_cv.predict(X_team)[0]
                
                # Apply recent form adjustment
                form_score = trend_features['recent_form_score']
                form_adjustment = -form_score * 0.05
                adjusted_prediction = predicted_pos + form_adjustment
                adjusted_prediction = max(1, min(20, adjusted_prediction))
                
                fold_predictions.append(adjusted_prediction)
                fold_actuals.append(row[target])
        
        if len(fold_predictions) > 0:
            fold_score = r2_score(fold_actuals, fold_predictions)
            cv_scores.append(fold_score)
    
    return np.mean(cv_scores) if cv_scores else 0.0

# Train final model on all historical data
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

## Random Forest Regressor
rf_model = RandomForestRegressor(n_estimators=100, random_state=42)
rf_model.fit(X_train, y_train)

# Function to Predict for a Specified Year using recent form analysis with confidence intervals
def compare_predictions_with_confidence(teams, actual_standings, prediction_year, confidence_level=0.95):
    # Filter the data for seasons that occurred before the given prediction year
    historical_data = data[data['season_end_year'] < prediction_year]

    if historical_data.empty:
        raise ValueError(f"No historical data available before {prediction_year}.")

    # Retrain model on historical data only
    X_historical = historical_data[features]
    y_historical = historical_data[target]
    
    rf_model.fit(X_historical, y_historical)

    predictions = []
    confidence_intervals = []
    recent_form_details = []
    
    print(f"Calculating confidence intervals using bootstrap sampling...")
    
    for i, team in enumerate(teams, 1):
        print(f"Processing {team} ({i}/{len(teams)})...", end=' ')
        
        # Get historical data for the specific team
        team_data = historical_data[historical_data['team'] == team]

        if team_data.empty:
            print(f"Warning: No historical data found for team '{team}'. Skipping prediction.")
            continue        # Calculate confidence intervals using bootstrap sampling
        ci_results = bootstrap_confidence_interval_recent_form(
            historical_data, team_data, features, prediction_year, 
            n_bootstrap=100, confidence_level=confidence_level  # Reduced for faster execution
        )
        
        # Calculate the weighted average of past performance metrics for the team
        trend_features = weighted_average(team_data, features, prediction_year)

        # Use only the original features for prediction
        X_team = trend_features[features].values.reshape(1, -1)

        # Predict the position using the model
        predicted_pos = rf_model.predict(X_team)[0]
        
        # Apply recent form adjustment using the tuned conservative factor
        form_score = trend_features['recent_form_score']
        form_adjustment = -form_score * 0.05  # Conservative adjustment factor
        adjusted_prediction = predicted_pos + form_adjustment
        
        # Ensure prediction stays within reasonable bounds (1-20)
        adjusted_prediction = max(1, min(20, adjusted_prediction))
        
        # Store confidence interval information
        confidence_intervals.append({
            'team': team,
            'prediction': ci_results['prediction'],
            'lower_bound': ci_results['lower_bound'],
            'upper_bound': ci_results['upper_bound'],
            'standard_error': ci_results['standard_error'],
            'interval_width': ci_results['upper_bound'] - ci_results['lower_bound']
        })
        
        # Store details for analysis (only significant form changes)
        if abs(form_score) > 0.5:  # Only show significant form changes
            form_direction = "Improving ↗" if form_score > 0 else "Declining ↘" if form_score < -0.5 else "Stable →"
            recent_form_details.append({
                'team': team,
                'form_score': form_score,
                'direction': form_direction,
                'adjustment': form_adjustment
            })
        
        # Store the adjusted result
        predictions.append({'Team': team, 'Predicted Position': adjusted_prediction})
        print("✓")

    # Sort predictions by predicted positions
    predictions.sort(key=lambda x: x['Predicted Position'])

    # Assign unique integer positions based on the sorted predictions
    for rank, prediction in enumerate(predictions, start=1):
        prediction['Predicted Position'] = rank

    # Convert predictions to DataFrame
    predicted_df = pd.DataFrame(predictions)
    
    # Merge predicted positions with actual positions
    actual_df = pd.DataFrame(actual_standings, columns=['Team', 'Actual Position'])
    comparison_df = pd.merge(predicted_df, actual_df, on='Team')

    # Calculate the difference between predicted and actual positions
    comparison_df['Position Difference'] = comparison_df['Predicted Position'] - comparison_df['Actual Position']

    # Add confidence interval information to comparison DataFrame
    ci_df = pd.DataFrame(confidence_intervals)
    comparison_df = pd.merge(comparison_df, ci_df, left_on='Team', right_on='team')

    # Save the comparison results to a CSV file
    output_file = f'{prediction_year}_recent_form_predictions_with_confidence.csv'
    comparison_df.to_csv(output_file, index=False)
    
    # Calculate the R² score
    r2 = r2_score(comparison_df['Actual Position'], comparison_df['Predicted Position'])
    
    # Perform cross-validation
    cv_score = cross_validate_time_series(data, features, target, prediction_year)
    
    print("\n" + "=" * 70)
    print("PREMIER LEAGUE 2024 PREDICTION RESULTS (RECENT FORM + CONFIDENCE INTERVALS)")
    print("=" * 70)
    print(f"Model Accuracy (R² Score): {r2:.3f}")
    print(f"Cross-Validation Score: {cv_score:.3f}")
    print(f"Confidence Level: {confidence_level*100:.0f}%")
    print(f"Results saved to: {output_file}")
    
    # Show recent form analysis
    if recent_form_details:
        print(f"\nRECENT FORM ANALYSIS:")
        print("-" * 40)
        for detail in recent_form_details:
            print(f"• {detail['team']:<17} Form Score: {detail['form_score']:+.2f} ({detail['direction']}) Adj: {detail['adjustment']:+.2f}")
    
    # Show predictions with confidence intervals
    print(f"\nPREDICTIONS WITH {confidence_level*100:.0f}% CONFIDENCE INTERVALS:")
    print("-" * 65)
    print("Team                 Pred  Actual  Diff   95% CI Range    Width")
    print("-" * 65)
    
    for i, row in comparison_df.head(20).iterrows():
        diff = row['Position Difference']
        if abs(diff) <= 1:
            icon = "✓"
        elif abs(diff) <= 2:
            icon = "~"
        else:
            icon = "✗"
        
        ci_range = f"[{row['lower_bound']:4.1f}, {row['upper_bound']:4.1f}]"
        width = row['interval_width']
        
        print(f"{icon} {row['Team']:<17} {row['Predicted Position']:4.0f}  {row['Actual Position']:6.0f}  {diff:+4.0f}   {ci_range:12}  {width:5.1f}")
    
    # Show teams with narrowest and widest confidence intervals
    print(f"\nCONFIDENCE INTERVAL ANALYSIS:")
    print("-" * 40)
    
    # Most confident predictions (narrowest intervals)
    narrowest_ci = comparison_df.nsmallest(3, 'interval_width')
    print("Most confident predictions (narrow intervals):")
    for i, row in narrowest_ci.iterrows():
        print(f"• {row['Team']:<17} Width: {row['interval_width']:4.1f} positions")
    
    # Least confident predictions (widest intervals)
    widest_ci = comparison_df.nlargest(3, 'interval_width')
    print("\nLeast confident predictions (wide intervals):")
    for i, row in widest_ci.iterrows():
        print(f"• {row['Team']:<17} Width: {row['interval_width']:4.1f} positions")
    
    # Show biggest errors
    biggest_errors = comparison_df.reindex(comparison_df['Position Difference'].abs().sort_values(ascending=False).index).head(3)
    print(f"\nBIGGEST PREDICTION ERRORS:")
    print("-" * 30)
    for i, row in biggest_errors.iterrows():
        ci_contains_actual = row['lower_bound'] <= row['Actual Position'] <= row['upper_bound']
        contains_text = "✓ within CI" if ci_contains_actual else "✗ outside CI"
        print(f"• {row['Team']:<17} Off by {abs(row['Position Difference']):.0f} positions ({contains_text})")
    
    # Calculate percentage of actual positions within confidence intervals
    within_ci = sum(1 for _, row in comparison_df.iterrows() 
                   if row['lower_bound'] <= row['Actual Position'] <= row['upper_bound'])
    ci_coverage = within_ci / len(comparison_df) * 100
    
    print(f"\nCONFIDENCE INTERVAL COVERAGE:")
    print(f"• {within_ci}/{len(comparison_df)} actual positions within {confidence_level*100:.0f}% CI ({ci_coverage:.1f}%)")
    print(f"• Average CI width: {comparison_df['interval_width'].mean():.1f} positions")
    
    print("=" * 70)
    
    return comparison_df

# Hardcoded actual 2024 standings for comparison
actual_standings_2024 = [
    {'Team': 'Manchester City', 'Actual Position': 1},
    {'Team': 'Arsenal', 'Actual Position': 2},
    {'Team': 'Liverpool', 'Actual Position': 3},
    {'Team': 'Aston Villa', 'Actual Position': 4},
    {'Team': 'Tottenham', 'Actual Position': 5},
    {'Team': 'Chelsea', 'Actual Position': 6},
    {'Team': 'Newcastle Utd', 'Actual Position': 7},
    {'Team': 'Manchester Utd', 'Actual Position': 8},
    {'Team': 'West Ham', 'Actual Position': 9},
    {'Team': 'Crystal Palace', 'Actual Position': 10},
    {'Team': 'Brighton', 'Actual Position': 11},
    {'Team': 'Bournemouth', 'Actual Position': 12},
    {'Team': 'Fulham', 'Actual Position': 13},
    {'Team': 'Wolves', 'Actual Position': 14},
    {'Team': 'Everton', 'Actual Position': 15},
    {'Team': 'Brentford', 'Actual Position': 16},
    {'Team': 'Nottingham Forest', 'Actual Position': 17},
    {'Team': 'Luton Town', 'Actual Position': 18},
    {'Team': 'Burnley', 'Actual Position': 19},
    {'Team': 'Sheffield Utd', 'Actual Position': 20},
]

teams_2024 = ['Manchester City', 'Arsenal', 'Liverpool', 'Aston Villa', 'Tottenham', 'Chelsea', 'Newcastle Utd', 'Manchester Utd', 'West Ham', 'Crystal Palace', 'Brighton', 'Bournemouth', 'Fulham', 'Wolves', 'Everton', 'Brentford', 'Nottingham Forest', 'Luton Town', 'Burnley', 'Sheffield Utd']

# Call function to compare predictions for 2024 using recent form approach with confidence intervals
compare_predictions_with_confidence(teams_2024, actual_standings_2024, 2024, confidence_level=0.95)
